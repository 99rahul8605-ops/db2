#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p logs
screen -S lookupimport -X quit >/dev/null 2>&1 || true
screen -L -Logfile "$PWD/logs/import.log" -dmS lookupimport bash -lc "cd '$PWD' && exec ./venv/bin/python3 import_users.py"
sleep 1; screen -ls | grep lookupimport || true
echo "Log: $PWD/logs/import.log"
