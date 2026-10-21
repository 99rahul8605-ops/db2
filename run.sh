#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(pwd -P)"
ACTION="${1:-start}"
SESSION=hitekbot
LOG="$ROOT/bot.log"

has_screen() { command -v screen >/dev/null && screen -ls 2>/dev/null | grep -Eq "[0-9]+\.${SESSION}[[:space:]]"; }
setup() {
  if [[ ! -x "$ROOT/venv/bin/python3" ]] || ! "$ROOT/venv/bin/python3" -c 'import sys' >/dev/null 2>&1; then
    echo '[setup] Creating Python venv...'
    command -v python3 >/dev/null || { echo 'Python3 missing; install python3 first'; exit 1; }
    if ! python3 -m venv "$ROOT/venv"; then
      if [[ "$(id -u)" == 0 ]] && command -v apt-get >/dev/null; then
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip
        python3 -m venv "$ROOT/venv"
      else
        echo 'Install python3-venv using your package manager, then retry.'; exit 1
      fi
    fi
  fi
  if ! command -v screen >/dev/null; then
    if [[ "$(id -u)" == 0 ]] && command -v apt-get >/dev/null; then
      apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y screen
    else echo 'screen missing; install it first.'; exit 1; fi
  fi
  if ! command -v 7z >/dev/null && ! command -v 7zz >/dev/null; then
    if [[ "$(id -u)" == 0 ]] && command -v apt-get >/dev/null; then
      echo '[setup] Installing 7-Zip for split archives...'
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y p7zip-full || \
        DEBIAN_FRONTEND=noninteractive apt-get install -y 7zip
    else
      echo '7z/7zz missing. Install 7-Zip using your package manager.'; exit 1
    fi
  fi
  [[ -f requirements.txt ]] || { echo 'requirements.txt missing'; exit 1; }
  if ! "$ROOT/venv/bin/python3" -c 'import pyrogram,fastapi,uvicorn,ijson,dotenv' >/dev/null 2>&1; then
    echo '[setup] Installing Python dependencies...'
    "$ROOT/venv/bin/python3" -m pip install -r requirements.txt
  fi
  if [[ ! -f .env ]]; then
    if [[ -f env.example ]]; then
      cp env.example .env
      chmod 600 .env
      echo 'Created .env from env.example. Fill bot token and ADMIN_IDS, then rerun.'
    else echo 'Missing .env; configure it first.'; fi
    exit 2
  fi
  echo '[setup] Ready.'
}
case "$ACTION" in
  setup|install) setup ;;
  start)
    if has_screen; then echo "Bot already running in screen: $SESSION"; exit 0; fi
    setup
    echo "[start] Starting $SESSION (log: $LOG)"
    screen -dmS "$SESSION" bash -c 'cd "$1" && exec "$2" -u "$3" >> "$4" 2>&1' _ "$ROOT" "$ROOT/venv/bin/python3" "$ROOT/bot.py" "$LOG"
    sleep 3
    if has_screen; then echo "Bot started. Check: bash run.sh status";
    else echo "Bot stopped during startup. Last log lines:"; tail -n 40 "$LOG" 2>/dev/null || true; exit 1; fi
    ;;
  status) if has_screen; then screen -ls; else echo "Bot screen $SESSION not running"; fi ;;
  logs) tail -n 70 "$LOG" 2>/dev/null || echo 'No bot log yet' ;;
  follow) touch "$LOG"; tail -f "$LOG" ;;
  stop)
    if has_screen; then screen -S "$SESSION" -X quit; echo 'Stopped bot screen.'; else echo 'Bot screen not running.'; fi ;;
  restart) bash "$ROOT/run.sh" stop; bash "$ROOT/run.sh" start ;;
  tidy)
    echo '[tidy] Moving legacy .sh files out of project root (not deleting).'
    backup="$ROOT/.legacy-shell-backup/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup"
    moved=0
    for old in install.sh start.sh run_screen.sh start_all.sh start_api.sh \
               run_api_screen.sh run_import_screen.sh run_manager_screen.sh \
               start_manager.sh stop.sh stop_all.sh status.sh install_autostart.sh; do
      if [[ -f "$ROOT/$old" ]]; then
        mv "$ROOT/$old" "$backup/$old"
        moved=$((moved+1))
      fi
    done
    echo "[tidy] Moved $moved legacy scripts into $backup"
    ;;
  foreground) setup; exec "$ROOT/venv/bin/python3" -u "$ROOT/bot.py" ;;
  *) echo "Usage: bash run.sh [start|setup|status|logs|follow|stop|restart|foreground|tidy]"; exit 2 ;;
esac
