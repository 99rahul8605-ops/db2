#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd -P)"
if [[ "$(id -u)" != 0 ]]; then echo 'Run as root'; exit 1; fi
if ! command -v systemctl >/dev/null || [[ ! -d /run/systemd/system ]]; then
  echo 'systemd unavailable in this container; use existing screen or hosting startup hook.'
  exit 2
fi
if [[ ! -x "$ROOT/venv/bin/python3" ]]; then echo 'venv missing'; exit 3; fi
if [[ ! -f "$ROOT/.env" ]]; then echo '.env missing'; exit 4; fi
cat > /etc/systemd/system/db2-bot.service <<EOF
[Unit]
Description=DB2 Telegram Admin Bot
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/venv/bin/python3 $ROOT/bot.py
Restart=on-failure
RestartSec=8
StandardOutput=append:$ROOT/bot.log
StandardError=append:$ROOT/bot.log
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/db2-api.service <<EOF
[Unit]
Description=DB2 FastAPI server
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$ROOT/venv/bin/python3 -m uvicorn api:app --host 127.0.0.1 --port 8080 --workers 1
Restart=on-failure
RestartSec=8
StandardOutput=append:$ROOT/api.log
StandardError=append:$ROOT/api.log
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/db2-tunnel.service <<EOF
[Unit]
Description=Cloudflare db2 API Tunnel
Wants=network-online.target
After=network-online.target db2-api.service
[Service]
Type=simple
ExecStart=/usr/bin/env cloudflared tunnel --config /root/.cloudflared/db2-api.yml run
Restart=on-failure
RestartSec=8
StandardOutput=append:$ROOT/tunnel.log
StandardError=append:$ROOT/tunnel.log
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable db2-bot db2-api db2-tunnel
cat <<EOF
Services installed and ENABLED for next boot, not started now.
Existing screen sessions may be running; DO NOT start duplicate services.
After scheduling maintenance, stop screen-managed bot/API/tunnel and execute:
  systemctl start db2-bot db2-api db2-tunnel
  systemctl status db2-bot db2-api db2-tunnel --no-pager
IMPORTANT: /api_start and /tunnel_start control bot-managed processes; systemd-managed services should be maintained with systemctl and don't use those start commands.
EOF
