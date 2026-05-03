#!/usr/bin/env bash
# Creates a 1 GiB swap file. Run before pip install to survive OOM during builds.
set -euo pipefail

SWAP_FILE="/swapfile"

if swapon --show | grep -q "$SWAP_FILE"; then
    echo "Swap already active at $SWAP_FILE — skipping."
    exit 0
fi

echo "Creating 1 GiB swap file at $SWAP_FILE…"
sudo fallocate -l 1G "$SWAP_FILE"
sudo chmod 600 "$SWAP_FILE"
sudo mkswap "$SWAP_FILE"
sudo swapon "$SWAP_FILE"

# Persist across reboots
if ! grep -q "$SWAP_FILE" /etc/fstab; then
    echo "$SWAP_FILE none swap sw 0 0" | sudo tee -a /etc/fstab
fi

# Reduce swap aggressiveness (prefer RAM)
echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf
sudo sysctl vm.swappiness=10

echo "Swap configured:"
swapon --show
