#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$project_dir/.venv/Scripts/python.exe" ]]; then
  printf '%s\n' 'Run bash setup.sh first (Windows Git Bash).' >&2
  exit 2
fi
cd -- "$project_dir"
exec "$project_dir/.venv/Scripts/python.exe" "$project_dir/watcher.py" "$@"
