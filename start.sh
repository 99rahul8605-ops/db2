#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python3 bot.py
