#!/usr/bin/env bash
for s in lookupapi lookupmanager lookupimport; do screen -S "$s" -X quit >/dev/null 2>&1 || true; done
