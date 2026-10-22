#!/usr/bin/env bash
# DB2 one-command launcher. Safe to rerun; does not stop already running workers.
set -Eeuo pipefail
cd /root/db2 || { echo 'ERROR: /root/db2 missing'; exit 1; }
ROOT=/root/db2
mkdir -p "$ROOT"

# Reuse the repository's package/venv installer; it does not start any services.
if [[ -f "$ROOT/run.sh" ]]; then
  bash "$ROOT/run.sh" setup
else
  echo 'ERROR: run.sh missing; install the latest DB2 bot package first.' >&2
  exit 1
fi

command -v screen >/dev/null || { echo 'ERROR: screen not installed'; exit 1; }
PY="$ROOT/venv/bin/python3"
[[ -x "$PY" ]] || { echo 'ERROR: venv Python missing'; exit 1; }

screen_exists() {
  screen -ls 2>/dev/null | grep -Eq "[[:digit:]]+\.${1}[[:space:]]"
}

# Bot: never start a second copy if already running, even outside screen.
if pgrep -f '[p]ython3? -u /root/db2/bot.py' >/dev/null || pgrep -f '[p]ython3? /root/db2/bot.py' >/dev/null; then
  echo '[bot] Already running; left unchanged.'
elif screen_exists hitekbot; then
  echo '[bot] hitekbot screen exists; inspect it before launching a duplicate.'
else
  screen -dmS hitekbot bash -lc 'cd /root/db2 && exec /root/db2/venv/bin/python3 -u bot.py >> /root/db2/bot.log 2>&1'
  echo '[bot] Started in screen: hitekbot'
fi

# API: the Cloudflare Tunnel forwards to 127.0.0.1:8080.
if curl -fsS --connect-timeout 2 --max-time 4 http://127.0.0.1:8080/health >/dev/null 2>&1; then
  echo '[api] Already healthy on 127.0.0.1:8080.'
elif (echo >/dev/tcp/127.0.0.1/8080) >/dev/null 2>&1; then
  echo '[api] Port 8080 is occupied but /health failed; NOT launching another server.'
elif screen_exists lookupapi; then
  echo '[api] lookupapi screen exists; inspect /root/db2/api.log before relaunch.'
else
  screen -dmS lookupapi bash -lc 'cd /root/db2 && exec /root/db2/venv/bin/python3 -m uvicorn api:app --host 127.0.0.1 --port 8080 >> /root/db2/api.log 2>&1'
  echo '[api] Started in screen: lookupapi'
fi

# Restore only the dedicated DB2 tunnel's config/credentials, never default config.yml.
if ! command -v cloudflared >/dev/null; then
  echo '[tunnel] ERROR: cloudflared missing. Other services left running.'
else
  if "$PY" -c 'from ops_control import ensure_tunnel_config; ensure_tunnel_config()' >/dev/null; then
    if pgrep -af '[c]loudflared' | grep -Fq '/root/.cloudflared/db2-api.yml'; then
      echo '[tunnel] DB2 tunnel process already running; left unchanged.'
    elif screen_exists db2tunnel; then
      echo '[tunnel] db2tunnel screen exists; inspect it before launching a duplicate.'
    else
      screen -dmS db2tunnel bash -lc 'exec cloudflared tunnel --config /root/.cloudflared/db2-api.yml run >> /root/db2/tunnel.log 2>&1'
      echo '[tunnel] Started in screen: db2tunnel'
    fi
  else
    echo '[tunnel] ERROR: cannot restore DB2 tunnel config/credentials; check cert.pem and tunnel ID.'
  fi
fi

sleep 2
echo
echo '=== SCREEN SESSIONS ==='
screen -ls || true
echo
echo '=== API LOCAL HEALTH ==='
curl -fsS --connect-timeout 2 --max-time 6 http://127.0.0.1:8080/health || echo 'API not yet healthy (check /root/db2/api.log)'
echo
echo 'Logs: tail -n 30 /root/db2/{bot,api,tunnel}.log'
echo 'Public: https://dbapi.teamsecret.in/health (requires connected tunnel and DNS)'
