#!/usr/bin/env bash
# One-shot EC2 ARM/Graviton2 bootstrap for BehavioralDummy.
# Run as: bash infra/scripts/bootstrap_ec2.sh
# Assumes Ubuntu 24.04 LTS (Noble) on t4g.small.
set -euo pipefail

APP_DIR="/home/ubuntu/MasterTheBehavioralInterview"
VENV_DIR="/home/ubuntu/.venv"

# ── 1. Swap (must come first — pip install may OOM otherwise) ─────────────────
bash "$APP_DIR/infra/scripts/setup_swap.sh"

# ── 2. System packages ────────────────────────────────────────────────────────
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3.12 python3.12-venv python3-pip \
    nginx \
    git \
    libjemalloc2 \
    certbot \
    python3-certbot-nginx \
    ca-certificates \
    curl

# docker.io was removed from Ubuntu 24.04 repos — install from Docker's official apt repo
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
ARCH=$(dpkg --print-architecture)
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -qq
sudo apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

# Node.js 20 via NodeSource — required for npm run build in deploy.sh
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y -qq nodejs

# ── 3. Verify jemalloc path (varies by distro) ───────────────────────────────
JEMALLOC_PATH=$(ldconfig -p | grep libjemalloc | awk '{print $NF}' | head -1)
echo "jemalloc found at: $JEMALLOC_PATH"
if [ -z "$JEMALLOC_PATH" ]; then
    echo "ERROR: libjemalloc2 not found after install — aborting."
    exit 1
fi

# Write verified path into the systemd env file template check
echo "Verify LD_PRELOAD=$JEMALLOC_PATH in infra/systemd/behavioral-dummy-env"

# ── 4. Python venv + dependencies ────────────────────────────────────────────
python3.12 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel --quiet
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

# ── 5. Nginx configuration ───────────────────────────────────────────────────
sudo cp "$APP_DIR/infra/nginx/behavioral-dummy.conf" \
    /etc/nginx/sites-available/behavioral-dummy
sudo ln -sf /etc/nginx/sites-available/behavioral-dummy \
    /etc/nginx/sites-enabled/behavioral-dummy
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

# ── 6. Frontend webroot ───────────────────────────────────────────────────────
sudo mkdir -p /var/www/behavioral-dummy/frontend
sudo chown -R ubuntu:ubuntu /var/www/behavioral-dummy

# ── 7. Systemd unit ───────────────────────────────────────────────────────────
sudo cp "$APP_DIR/infra/systemd/behavioral-dummy.service" \
    /etc/systemd/system/behavioral-dummy.service
sudo systemctl daemon-reload
sudo systemctl enable behavioral-dummy

# ── 8. Docker socket permission (for Compose observability stack) ─────────────
sudo usermod -aG docker ubuntu

# ── 9. Observability sidecar ─────────────────────────────────────────────────
cd "$APP_DIR/observability"
sudo docker compose up -d

echo ""
echo "Bootstrap complete."
echo ""
echo "Next steps:"
echo "  1. Fill in $APP_DIR/infra/systemd/behavioral-dummy-env (copy from .example)"
echo "  2. Run: alembic upgrade head"
echo "  3. Run: python ingestion/ingest.py --dir ingestion/anecdotes --recreate-index"
echo "  4. cd frontend && npm install && npm run build"
echo "  5. sudo systemctl start behavioral-dummy"
echo "  6. sudo certbot --nginx -d YOUR_DOMAIN_HERE"
