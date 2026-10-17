#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
screen -S hitekapi -X quit >/dev/null 2>&1 || true
screen -dmS hitekapi bash -lc "cd '$PWD' && ./start_api.sh"
sleep 1
screen -ls | grep hitekapi || true
