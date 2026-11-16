"""Check the staged file set before publication; never print matched secret values."""
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(['git','ls-files','--cached','-z'],cwd=root).decode().split('\0')
    failures = []
    checks = {
        'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'),
        'aws_access_key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    }
    for file in filter(None,files):
        path = Path(file)
        lowered = file.lower()
        if any(part.lower() in {'data','.venv','__pycache__','node_modules','.cloudflared','.aws','.codex','.agents'} for part in path.parts):
            failures.append((file,'runtime_or_private_directory'))
        if path.name in {'HANDOFF.md','config.json','.env'} or lowered.endswith(('.local.json','.local.yml','.local.yaml','.sqlite','.sqlite3','.db','.log','.pem','.key')):
            failures.append((file,'private_file'))
        if lowered.endswith(('.png','.jpg','.jpeg')):
            continue
        data = subprocess.check_output(['git','show',f':{file}'],cwd=root)
        for name, pattern in checks.items():
            if pattern.search(data):
                failures.append((file,name))
        if file.endswith('.svg') and (b'<script' in data or b'javascript:' in data or b'<foreignObject' in data):
            failures.append((file,'active_svg_content'))
    for file,reason in failures:
        print(f'BLOCKED: {file}: {reason}',file=sys.stderr)
    if failures:
        return 1
    print(f'PASS: {len(list(filter(None,files)))} staged files; no excluded runtime data or recognized secret patterns found.')
    print('This is a bounded publication check, not a comprehensive secret or security audit.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
