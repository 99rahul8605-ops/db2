#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/hitek-bot
SESSION=hitekbot
cd "$APP_DIR"

if screen -list | grep -q "[.]${SESSION}[[:space:]]"; then
  echo "Bot screen already exists: $SESSION"
  echo "View: screen -r $SESSION"
  exit 0
fi

screen -DmS "$SESSION" bash -lc "cd '$APP_DIR' && exec '$APP_DIR/venv/bin/python3' bot.py"
sleep 2
if screen -list | grep -q "[.]${SESSION}[[:space:]]"; then
  echo "Bot started in screen: $SESSION"
  echo "View: screen -r $SESSION"
else
  echo "Bot exited during startup. Run foreground for the error:"
  echo "cd $APP_DIR && $APP_DIR/venv/bin/python3 bot.py"
  exit 1
fi
