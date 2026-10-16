#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

# Stop detached screen session if present.
screen -S hitekbot -X quit 2>/dev/null || true

# Also stop a foreground/stale process started from this repo.
if [[ -f .bot.pid ]]; then
  pid="$(cat .bot.pid 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f .bot.pid
fi

echo "Stopped hitekbot (if it was running)."
