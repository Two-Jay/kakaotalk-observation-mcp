"""Durable incremental transcript storage. No dependency on a desktop or agent."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from watcher import digest, new_start, now, parse_chat


class TranscriptStore:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "collector.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints(room TEXT PRIMARY KEY, hashes TEXT, checked_at TEXT);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
                    record TEXT NOT NULL, collected_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_room ON messages(room, id);
                CREATE TABLE IF NOT EXISTS runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT, at TEXT, status TEXT,
                    added INTEGER, error_code TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, room, text):
        records = parse_chat(text)
        hashes = [record["hash"] for record in records]
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT hashes FROM checkpoints WHERE room=?", (room,)).fetchone()
            # Refuse ambiguous overlaps; a gap must never become a fresh baseline.
            start = new_start(json.loads(old[0]), hashes) if old else 0
            db.executemany("INSERT INTO messages(room,record,collected_at) VALUES (?,?,?)",
                           [(room, json.dumps(r, ensure_ascii=False), stamp) for r in records[start:]])
            db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?)",
                       (room, json.dumps(hashes[-2000:]), stamp))
            db.execute("INSERT INTO runs(room,at,status,added) VALUES (?,?,?,?)",
                       (room, stamp, "ok", len(records) - start))
        # SQLite is authoritative. Regenerate atomically, including on no-change runs:
        # a crash after commit cannot duplicate messages or permanently lose a TXT export.
        path = self.export(room)
        return {"status": "ok", "room": room, "initial_capture": old is None,
                "captured_messages": len(records), "new_messages": len(records) - start,
                "text_file": str(path), "coverage": "copied_history_only", **self.status(room)}

    def check_overlap(self, room, text):
        records = parse_chat(text)
        with self.connect() as db:
            old = db.execute("SELECT hashes FROM checkpoints WHERE room=?", (room,)).fetchone()
        if old:
            new_start(json.loads(old[0]), [r["hash"] for r in records])

    def export(self, room):
        path = self.folder / f"transcript-{digest(room)[:12]}.txt"
        temporary = None
        try:
            with self.connect() as db, tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                                                  dir=self.folder, suffix='.tmp', delete=False) as out:
                temporary = Path(out.name)
                for (payload,) in db.execute("SELECT record FROM messages WHERE room=? ORDER BY id", (room,)):
                    r = json.loads(payload)
                    out.write(f"{r['date_label'] or '(날짜 미상)'} [{r['sender']}] [{r['time_label']}] {r['message']}\n")
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return path

    def failure(self, room, code):
        with self.connect() as db:
            db.execute("INSERT INTO runs(room,at,status,added,error_code) VALUES (?,?,?,0,?)",
                       (room, now(), "error", code))

    def status(self, room):
        with self.connect() as db:
            row = db.execute("SELECT checked_at FROM checkpoints WHERE room=?", (room,)).fetchone()
            total = db.execute("SELECT count(*) FROM messages WHERE room=?", (room,)).fetchone()[0]
            last_run = db.execute("SELECT at,status,added,error_code FROM runs WHERE room=? ORDER BY id DESC LIMIT 1", (room,)).fetchone()
        return {"total_messages": total, "last_success_at": row[0] if row else None,
                "last_run": dict(zip(("at", "status", "added", "error_code"), last_run)) if last_run else None}
