"""Local publisher. Reads existing SQLite files in read-only mode; never operates GUI."""
import argparse
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from remote_config import load_config


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    return connection


def build_snapshot(config, folder):
    if not config.enabled:
        raise ValueError('원격 공유가 꺼져 있습니다.')
    folder = Path(folder)
    stamp = time.time()
    policies = []
    with ExitStack() as stack:
        source = stack.enter_context(closing(readonly(folder/'monitor.sqlite3')))
        archive = stack.enter_context(closing(readonly(folder/'collector.sqlite3')))
        for policy_id, alias in config.shared_policies:
            row = source.execute('SELECT config FROM policies WHERE id=? AND deleted=0', (policy_id,)).fetchone()
            if not row:
                continue
            policy = json.loads(row['config'])
            last = source.execute('SELECT status,finished FROM jobs WHERE policy_id=? ORDER BY created DESC LIMIT 1', (policy_id,)).fetchone()
            success = archive.execute('SELECT checked_at FROM checkpoints WHERE room=?', (policy['room'],)).fetchone()
            # No record/config/room/keywords/error/path fields enter the shared snapshot.
            count = source.execute('SELECT count(*),max(at) FROM mentions WHERE policy_id=? AND at>=?', (policy_id, stamp-86400)).fetchone()
            policies.append({'alias':alias, 'automatic_target':bool(policy['enabled']),
                             'check_status':last['status'] if last else 'never',
                             'last_check_finished':last['finished'] if last else None,
                             'last_collection_success':success['checked_at'] if success else None,
                             'mentions_last_24h':count[0], 'last_mention_at':count[1]})
    return {'schema_version':1, 'selection_revision':config.selection_revision, 'generated_at':stamp, 'policies':policies}


def publish(config, folder, output):
    snapshot = build_snapshot(config, folder)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False, suffix='.tmp') as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    while True:
        try:
            config = load_config(args.config)
            publish(config, args.data, args.output)
        except Exception as exc:
            import sys
            print(f'publish_failed: {type(exc).__name__}',file=sys.stderr,flush=True)
            # Do not leave yesterday's authorized data readable after sharing is disabled.
            if args.output.exists():
                args.output.unlink()
            if not args.watch:
                raise SystemExit(2)
        if not args.watch:
            break
        time.sleep(15)
