#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
apt-get update
apt-get install -y python3 python3-venv screen curl sqlite3
if [[ ! -d venv ]]; then python3 -m venv venv; fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
if [[ ! -f .env ]]; then cp env.example .env; chmod 600 .env; echo "Created .env - edit it now."; fi
mkdir -p logs /root/db2/lookup
chmod +x *.sh *.py
echo "Install complete."
