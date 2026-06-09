#!/bin/bash
# Cloud-init User Data script — runs as root on EC2 first boot.
# Paste this into EC2 Launch Instance → Advanced details → User data.
#
# BEFORE LAUNCHING: replace REPO_URL with your actual repo URL.
# For private repos use: https://YOUR_PAT@github.com/YOUR_USERNAME/MasterTheBehavioralInterview.git
#
# Progress is logged to /var/log/behavioral-dummy-bootstrap.log.
# After this script completes, one SSH session is required to fill in secrets.
set -euo pipefail
exec > /var/log/behavioral-dummy-bootstrap.log 2>&1

REPO_URL="https://github.com/AngelGalindo7/BehavioralInterviewClone.git"
APP_DIR="/home/ubuntu/MasterTheBehavioralInterview"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting cloud-init bootstrap..."

# Ensure git is available (Ubuntu 24.04 minimal image includes it, but be safe)
apt-get update -qq
apt-get install -y -qq git

# Clone repo as ubuntu user
if [ -d "$APP_DIR" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $APP_DIR already exists — skipping clone."
else
    git clone "$REPO_URL" "$APP_DIR"
    chown -R ubuntu:ubuntu "$APP_DIR"
fi

# Run the bootstrap script as ubuntu.
# bootstrap_ec2.sh uses sudo internally for system-level operations — this is safe
# because ubuntu has passwordless sudo on AWS Ubuntu AMIs.
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running bootstrap_ec2.sh as ubuntu..."
sudo -u ubuntu bash "$APP_DIR/infra/scripts/bootstrap_ec2.sh"

echo ""
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Cloud-init bootstrap complete."
echo ""
echo "Next steps (see docs/SETUP.md Phase 6 onward):"
echo "  1. SSH in and fill infra/systemd/behavioral-dummy-env"
echo "     cp infra/systemd/behavioral-dummy-env.example infra/systemd/behavioral-dummy-env"
echo "     nano infra/systemd/behavioral-dummy-env"
echo "  2. Update domain + Grafana password in observability/docker-compose.yml,"
echo "     then restart Compose to apply:"
echo "     cd ~/MasterTheBehavioralInterview/observability && sudo docker compose up -d"
echo "  3. Replace domain placeholder in the installed nginx config, then reload:"
echo "     sudo sed -i 's/YOUR_DOMAIN_HERE/your-domain.com/g' /etc/nginx/sites-available/behavioral-dummy"
echo "     sudo nginx -t && sudo systemctl reload nginx"
echo "  4. Run: alembic upgrade head"
echo "  5. Build frontend:"
echo "     cd ~/MasterTheBehavioralInterview/frontend && npm install && npm run build"
echo "     sudo cp -r dist/* /var/www/behavioral-dummy/frontend/"
echo "  6. Run ingestion locally: python ingestion/ingest.py --dir ingestion/anecdotes --recreate-index"
echo "  7. Run: sudo certbot --nginx -d your-domain.com"
echo "  8. Build and start the app container:"
echo "     cd ~/MasterTheBehavioralInterview"
echo "     docker build -t behavioral-dummy:latest ."
echo "     docker compose -f docker-compose.app.yml up -d"
