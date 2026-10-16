#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -x venv/bin/python3 ]; then
  python3 -m venv venv
  venv/bin/pip install -U pip
  venv/bin/pip install -r requirements.txt
fi
exec venv/bin/python3 bot.py
