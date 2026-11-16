#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# PYTHON can be an executable path, including spaces (not a command with flags).
python_command=()
if [[ -n "${PYTHON:-}" ]]; then
  python_command=("$PYTHON")
else
  for candidate in python.exe python py.exe py python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      trial=("$candidate")
      [[ "$candidate" == py || "$candidate" == py.exe ]] && trial+=(-3)
      if "${trial[@]}" -c 'import sys; assert sys.platform == "win32" and sys.version_info >= (3, 11)' >/dev/null 2>&1; then
        python_command=("${trial[@]}")
        break
      fi
    fi
  done
fi
if [[ ${#python_command[@]} -eq 0 ]]; then
  printf '%s\n' 'Windows Python 3.11+ not found. Set PYTHON to its executable path.' >&2
  exit 2
fi
"${python_command[@]}" -c 'import sys; assert sys.platform == "win32" and sys.version_info >= (3, 11), "Use Windows Python 3.11+ in Git Bash, not WSL Python"'
if [[ ! -f .venv/Scripts/python.exe ]]; then
  "${python_command[@]}" -m venv .venv
fi
./.venv/Scripts/python.exe -m pip install -r requirements.txt
if [[ ! -e config.json ]]; then
  cp -- config.example.json config.json
fi
printf '%s\n' 'Setup complete. Edit config.json, then run: bash run.sh inspect'
