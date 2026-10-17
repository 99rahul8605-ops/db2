#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if screen -list | grep -q "\.lookupimport[[:space:]]"; then
  echo "lookupimport already running"
  exit 0
fi
screen -dmS lookupimport bash -lc "cd '$PWD' && ./venv/bin/python3 import_users.py 2>&1 | tee -a import.log"
echo "Import started. Attach: screen -r lookupimport"
