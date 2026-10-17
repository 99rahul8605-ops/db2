#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p logs
screen -S lookupapi -X quit >/dev/null 2>&1 || true
screen -L -Logfile "$PWD/logs/api.log" -dmS lookupapi bash -lc "cd '$PWD' && exec ./start_api.sh"
sleep 1; screen -ls | grep lookupapi || true
