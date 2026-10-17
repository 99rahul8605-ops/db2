#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
./stop.sh || true
for f in .api.pid .import.pid; do
  if [[ -f "$f" ]]; then
    pid=$(cat "$f" 2>/dev/null || true)
    if [[ -n "$pid" ]]; then kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true; fi
    rm -f "$f"
  fi
done
echo "Bot + API + importer stop requested."
