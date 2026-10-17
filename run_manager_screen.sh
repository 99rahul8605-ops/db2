#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p logs
screen -S lookupmanager -X quit >/dev/null 2>&1 || true
screen -L -Logfile "$PWD/logs/manager.log" -dmS lookupmanager bash -lc "cd '$PWD' && exec ./start_manager.sh"
sleep 1; screen -ls | grep lookupmanager || true
