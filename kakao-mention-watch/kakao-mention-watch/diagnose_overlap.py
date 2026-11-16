"""Print coverage metadata only; keep chat content out of diagnostic output."""
import argparse
import json
from collections import Counter
from collector_store import TranscriptStore
from watcher import parse_chat
from windows_collector import WindowsCollector, desktop_lock

p = argparse.ArgumentParser()
p.add_argument("--room", required=True)
p.add_argument("--output", required=True)
p.add_argument("--pageups", type=int, default=0)
a = p.parse_args()
with desktop_lock():
    records = parse_chat(WindowsCollector(a.room, a.output).read(a.pageups))
    store = TranscriptStore(a.output)
    with store.connect() as db:
        old = [json.loads(r[0]) for r in db.execute("SELECT record FROM messages WHERE room=? ORDER BY id", (a.room,))]
    def signature(r):
        return (r['sender'], r['time_label'], r['message'])
    print(json.dumps({"old_count": len(old), "current_count": len(records),
                      "old_days": dict(Counter(r['date_label'] for r in old)),
                      "current_days": dict(Counter(r['date_label'] for r in records)),
                      "exact_overlap": len({r['hash'] for r in old} & {r['hash'] for r in records}),
                      "overlap_without_day": len({signature(r) for r in old} & {signature(r) for r in records})}, ensure_ascii=True))
