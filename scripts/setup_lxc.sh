#!/usr/bin/env bash
# Run on the LXC HOST (not inside container) — example for LXD
set -euo pipefail

CONTAINER="${1:-odoo-agent}"

echo "Creating container: $CONTAINER"
lxc launch ubuntu:24.04 "$CONTAINER" || true
sleep 5

lxc exec "$CONTAINER" -- apt-get update
lxc exec "$CONTAINER" -- apt-get install -y python3 python3-venv python3-pip git curl ca-certificates

echo "Container ready. Next:"
echo "  lxc exec $CONTAINER -- bash"
echo "  cd /opt && git clone https://github.com/mahdi-m1/odoo-ai-agent-monitor.git"
echo "  cd odoo-ai-agent-monitor && bash scripts/install.sh"
