#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SESSION="hitekbot"

if screen -list | grep -q "\.${SESSION}[[:space:]]"; then
  echo "$SESSION is already running. Attach with: screen -r $SESSION"
  exit 0
fi

screen -dmS "$SESSION" bash -lc "cd '$PWD' && ./start.sh"
sleep 2
if screen -list | grep -q "\.${SESSION}[[:space:]]"; then
  echo "Started in screen: $SESSION"
  echo "Attach: screen -r $SESSION"
else
  echo "Bot exited during startup. Run ./start.sh in foreground to see the error."
  exit 1
fi
