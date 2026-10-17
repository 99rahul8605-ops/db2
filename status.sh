#!/usr/bin/env bash
echo "--- screens ---"; screen -ls || true
echo "--- lookup storage ---"; du -sh /root/db2/lookup 2>/dev/null || true
echo "--- disk ---"; df -h / | tail -1
