#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
./run_screen.sh
echo "Bot started. Use /buildall CONFIRM in Telegram; API is auto-managed."
