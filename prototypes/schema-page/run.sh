#!/usr/bin/env bash
# PROTOTYPE — throwaway (issue #8). One command to see the page.
cd "$(dirname "$0")" || exit 1
exec python3 app.py "$@"
