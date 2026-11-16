#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
destination_root="${1:-$HOME/project_personal}"
target="$destination_root/kakao-mention-watch"
if [[ -e "$target" ]]; then
  printf 'Target already exists; nothing was overwritten: %s\n' "$target" >&2
  exit 2
fi
mkdir -p -- "$destination_root"
mkdir -- "$target"
shopt -s dotglob nullglob
for item in "$source_dir"/*; do
  case "${item##*/}" in
    .venv|__pycache__|data|config.json) continue ;;
  esac
  cp -R -- "$item" "$target/"
done
printf 'Copied project to %s\n' "$target"
printf 'Next: cd "%s" && bash setup.sh\n' "$target"
