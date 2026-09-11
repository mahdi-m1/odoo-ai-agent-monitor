#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — edit Odoo credentials before running."
fi

mkdir -p reports_output data logs
echo "Install complete. Activate: source venv/bin/activate"
echo "Web: uvicorn web.app:app --host 0.0.0.0 --port 8080"
echo "CLI agent: python -m agent.main"
echo "Scheduler: python -m agent.scheduler"
