#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v screen >/dev/null || { apt-get update && apt-get install -y screen; }
screen -S hitekbot -X quit >/dev/null 2>&1 || true
screen -dmS hitekbot bash -lc 'cd "/mnt/data/hitek_upload_bot_simple" && ./start.sh'
echo "Started in screen: hitekbot"
echo "Open console: screen -r hitekbot"
