#!/usr/bin/env bash
# Deploy script — invoked by SSM Run Command from GitHub Actions, or run
# manually on the EC2 instance as the ubuntu user.
#
# SSM Run Command executes as ssm-user (passwordless sudo). The first block
# re-execs as ubuntu so git operations keep correct file ownership.
set -euo pipefail

# ── Re-exec as ubuntu if running as root / ssm-user ──────────────────────────
if [ "$(id -un)" != "ubuntu" ]; then
  exec sudo -H -u ubuntu bash "$0" "$@"
fi

APP_DIR="/home/ubuntu/MasterTheBehavioralInterview"

cd "$APP_DIR"

echo "==> Pulling latest code"
git fetch origin main
git reset --hard origin/main

# One-time migration: disable the old systemd unit so it does not race
# with the Docker container on port 8000.
if systemctl is-active --quiet behavioral-dummy 2>/dev/null; then
  echo "==> Stopping legacy systemd service"
  sudo systemctl stop behavioral-dummy
  sudo systemctl disable behavioral-dummy
fi

echo "==> Building frontend"
cd frontend
npm ci --silent
npm run build
cd ..

echo "==> Syncing frontend to Nginx webroot"
sudo rsync -av --delete frontend/dist/ /var/www/behavioral-dummy/frontend/

echo "==> Building Docker image (native ARM64)"
docker build -t behavioral-dummy:latest .

echo "==> Replacing running container"
docker compose -f docker-compose.app.yml up -d --force-recreate

echo "==> Running Alembic migrations"
docker exec behavioral-dummy alembic upgrade head

echo "==> Waiting for app to become healthy (max 60s)"
for i in $(seq 1 12); do
  if docker exec behavioral-dummy \
      curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "App is healthy."
    break
  fi
  echo "  attempt $i/12 — sleeping 5s"
  sleep 5
  if [ "$i" -eq 12 ]; then
    echo "ERROR: app did not become healthy. Last 50 log lines:"
    docker logs behavioral-dummy --tail 50
    exit 1
  fi
done

echo "==> Syncing Nginx config from repo"
sudo cp "$APP_DIR/infra/nginx/behavioral-dummy.conf" \
    /etc/nginx/sites-available/behavioral-dummy

echo "==> Reloading Nginx"
sudo nginx -t && sudo systemctl reload nginx

echo "==> Pruning Docker artifacts"
docker image prune -f
docker builder prune --keep-storage 2GB -f

echo ""
echo "==> Deploy complete."
docker compose -f docker-compose.app.yml ps
