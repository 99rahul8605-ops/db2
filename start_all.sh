#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
./run_api_screen.sh
./run_manager_screen.sh
