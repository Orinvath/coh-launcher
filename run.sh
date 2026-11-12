#!/usr/bin/env bash
# Starts the launcher with the Python in this project's PATH. Run from any folder.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 launcher.py "$@"
