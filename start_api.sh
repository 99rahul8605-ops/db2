#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
exec ./venv/bin/uvicorn api:app --host "${BIND_HOST:-127.0.0.1}" --port "${BIND_PORT:-8080}" --workers 1
