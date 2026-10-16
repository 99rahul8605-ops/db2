#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh"
  exit 1
fi

APP_DIR=/opt/hitek-bot
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv p7zip-full rsync

mkdir -p "$APP_DIR" "$APP_DIR/uploads" "$APP_DIR/extracted" "$APP_DIR/session"
cp "$SRC_DIR/bot.py" "$APP_DIR/bot.py"
cp "$SRC_DIR/requirements.txt" "$APP_DIR/requirements.txt"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$SRC_DIR/.env.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo
  echo "Created $APP_DIR/.env"
  echo "EDIT IT before starting: nano $APP_DIR/.env"
fi

cp "$SRC_DIR/hitek-upload-bot.service" /etc/systemd/system/hitek-upload-bot.service
systemctl daemon-reload

echo
echo "Install complete."
echo "1) nano /opt/hitek-bot/.env"
echo "2) systemctl enable --now hitek-upload-bot"
echo "3) journalctl -u hitek-upload-bot -f"
