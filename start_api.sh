#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a
. ./.env
set +a
exec ./venv/bin/uvicorn api:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8080}" --workers 1
