#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERROR: .env missing. Copy .env.example to .env and fill credentials."
  exit 1
fi

if [[ ! -x venv/bin/python3 ]]; then
  echo "Creating venv..."
  python3 -m venv venv
fi

# Keep deps current after git pull; quiet when already satisfied.
venv/bin/pip install -q -r requirements.txt
exec venv/bin/python3 bot.py
