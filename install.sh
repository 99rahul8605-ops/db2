#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh"
  exit 1
fi

APP_DIR=/opt/hitek-bot
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv p7zip-full rsync screen

mkdir -p "$APP_DIR" "$APP_DIR/uploads" "$APP_DIR/extracted" "$APP_DIR/session"
rsync -a --exclude '.env' --exclude 'venv' --exclude 'uploads' --exclude 'extracted' --exclude 'session' \
  "$SRC_DIR/" "$APP_DIR/"

if [[ ! -d "$APP_DIR/venv" ]]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$SRC_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo
  echo "Created $APP_DIR/.env"
  echo "EDIT IT: nano $APP_DIR/.env"
else
  echo "Keeping existing $APP_DIR/.env (not overwritten)."
fi

chmod +x "$APP_DIR/start.sh" "$APP_DIR/stop.sh" 2>/dev/null || true

if command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm= 2>/dev/null || true)" == "systemd" ]]; then
  cp "$SRC_DIR/hitek-upload-bot.service" /etc/systemd/system/hitek-upload-bot.service
  systemctl daemon-reload
  echo
  echo "Install complete (systemd detected)."
  echo "Start: systemctl enable --now hitek-upload-bot"
  echo "Logs:  journalctl -u hitek-upload-bot -f"
else
  echo
  echo "Install complete (container/non-systemd environment detected)."
  echo "1) nano /opt/hitek-bot/.env"
  echo "2) /opt/hitek-bot/start.sh"
  echo "3) screen -r hitekbot    # view bot"
fi
