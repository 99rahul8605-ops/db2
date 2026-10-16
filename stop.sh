#!/usr/bin/env bash
set -euo pipefail
screen -S hitekbot -X quit 2>/dev/null || true
echo "Stopped hitekbot screen (if it was running)."
