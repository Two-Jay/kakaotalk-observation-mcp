"""Shared policy, job and mention storage for the local website and stdio MCP."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unicodedata
import uuid

from collector_store import TranscriptStore


def normalized(text):
    return unicodedata.normalize("NFC", text).casefold()


def validate_policy(value):
    if not isinstance(value, dict):
        raise ValueError("정책은 객체여야 합니다.")
    room = value.get("room")
    keywords = value.get("keywords")
    interval = value.get("interval_seconds", 300)
    enabled = value.get("enabled", False)
    name = value.get("name", room)
    if not isinstance(room, str) or not room.strip() or len(room) > 200 or any(c in room for c in "\n\r\t"):
        raise ValueError("톡방 이름은 1~200자의 한 줄 문자열이어야 합니다.")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("정책 이름은 1~200자여야 합니다.")
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 50 or any(
            not isinstance(k, str) or not k.strip() or len(k) > 100 for k in keywords):
        raise ValueError("키워드는 1~100자 문자열을 1~50개 입력하세요.")
    if type(interval) is not int or not 60 <= interval <= 86400:
        raise ValueError("점검 주기는 60~86400초 사이의 정수여야 합니다.")
    if type(enabled) is not bool:
        raise ValueError("enabled는 true 또는 false여야 합니다.")
    return {"name": name.strip(), "room": room.strip(),
            "keywords": list(dict.fromkeys(k.strip() for k in keywords)),
            "interval_seconds": interval, "enabled": enabled}


class MonitorService:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.store = TranscriptStore(self.folder)
        self.path = self.folder / "monitor.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
                INSERT OR IGNORE INTO settings VALUES('scheduler_enabled', 'false');
                CREATE TABLE IF NOT EXISTS policies(
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, cursor INTEGER NOT NULL,
                    next_run REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, policy_id TEXT, config TEXT, status TEXT,
                    created REAL, started REAL, finished REAL, result TEXT, error TEXT,
                    source TEXT NOT NULL DEFAULT 'manual');
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_policy
                    ON jobs(policy_id) WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS mentions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, policy_id TEXT, message_id INTEGER,
                    room TEXT, keywords TEXT, record TEXT, at REAL, UNIQUE(policy_id,message_id));
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, action TEXT, detail TEXT);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def max_message(self, room):
        with self.store.connect() as db:
            return db.execute("SELECT coalesce(max(id),0) FROM messages WHERE room=?", (room,)).fetchone()[0]

    def audit(self, db, action, detail):
        db.execute("INSERT INTO audit(at,action,detail) VALUES(?,?,?)", (time.time(), action, detail))

    def list_policies(self):
        with self.db() as db:
            rows = db.execute("SELECT * FROM policies WHERE deleted=0 ORDER BY updated DESC").fetchall()
        return [{"id": r['id'], **json.loads(r['config']), "next_run": r['next_run'],
                 "collection": self.store.status(json.loads(r['config'])['room'])} for r in rows]

    def save_policy(self, value, policy_id=None):
        config = validate_policy(value)
        stamp = time.time()
        cursor = self.max_message(config['room'])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if policy_id:
                old = db.execute("SELECT * FROM policies WHERE id=? AND deleted=0", (policy_id,)).fetchone()
                if not old:
                    raise ValueError("정책을 찾을 수 없습니다.")
                old_config = json.loads(old['config'])
                if old_config['room'] == config['room'] and old_config['keywords'] == config['keywords']:
                    cursor = old['cursor']
                db.execute("UPDATE policies SET config=?,cursor=?,next_run=?,updated=? WHERE id=?",
                           (json.dumps(config, ensure_ascii=False), cursor, stamp+config['interval_seconds'], stamp, policy_id))
                db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE policy_id=? AND status='queued'", (stamp, policy_id))
            else:
                policy_id = uuid.uuid4().hex
                db.execute("INSERT INTO policies(id,config,cursor,next_run,updated) VALUES(?,?,?,?,?)",
                           (policy_id, json.dumps(config, ensure_ascii=False), cursor, stamp+config['interval_seconds'], stamp))
            self.audit(db, "policy.saved", config['name'])
        return {"id": policy_id, **config}

    def delete_policy(self, policy_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM policies WHERE id=? AND deleted=0", (policy_id,)).fetchone():
                raise ValueError("정책을 찾을 수 없습니다.")
            db.execute("UPDATE policies SET deleted=1 WHERE id=?", (policy_id,))
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE policy_id=? AND status='queued'", (time.time(), policy_id))
            self.audit(db, "policy.deleted", policy_id)
        return {"deleted": policy_id, "note": "기존 대화·감지·로그는 보존됩니다. 실행 중인 점검은 끝까지 완료됩니다."}

    def scheduler(self, enabled=None):
        with self.db() as db:
            if enabled is not None:
                if type(enabled) is not bool:
                    raise ValueError("자동 점검 값은 true 또는 false여야 합니다.")
                db.execute("UPDATE settings SET value=? WHERE key='scheduler_enabled'", (json.dumps(enabled),))
                if not enabled:
                    db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE source='auto' AND status='queued'", (time.time(),))
                self.audit(db, "scheduler.changed", str(enabled))
            value = db.execute("SELECT value FROM settings WHERE key='scheduler_enabled'").fetchone()[0]
        return json.loads(value)

    def enqueue(self, policy_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._enqueue(db, policy_id)

    def _enqueue(self, db, policy_id, source='manual'):
        policy = db.execute("SELECT * FROM policies WHERE id=? AND deleted=0", (policy_id,)).fetchone()
        if not policy:
            raise ValueError("정책을 찾을 수 없습니다.")
        existing = db.execute("SELECT id,status FROM jobs WHERE policy_id=? AND status IN ('queued','running')", (policy_id,)).fetchone()
        if existing:
            return dict(existing)
        job_id = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,policy_id,config,status,created,source) VALUES(?,?,?,'queued',?,?)",
                   (job_id, policy_id, policy['config'], time.time(), source))
        return {"id": job_id, "status": "queued"}

    def schedule_due(self, stamp=None):
        stamp = time.time() if stamp is None else stamp
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM settings WHERE key='scheduler_enabled'").fetchone()[0] != 'true':
                return
            for row in db.execute("SELECT * FROM policies WHERE deleted=0 AND next_run<=?", (stamp,)).fetchall():
                config = json.loads(row['config'])
                if config['enabled']:
                    self._enqueue(db, row['id'], source='auto')
                    db.execute("UPDATE policies SET next_run=? WHERE id=?", (stamp+config['interval_seconds'], row['id']))

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running',started=? WHERE id=?", (time.time(), row['id']))
                return dict(row)

    def scan_mentions(self, policy_id, config):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            policy = db.execute("SELECT * FROM policies WHERE id=? AND deleted=0", (policy_id,)).fetchone()
            if not policy or json.loads(policy['config']) != config:
                return 0  # Configuration changed during capture; don't advance its new cursor.
            with self.store.connect() as source:
                records = source.execute("SELECT id,record FROM messages WHERE room=? AND id>? ORDER BY id", (config['room'], policy['cursor'])).fetchall()
            count = 0
            for message_id, payload in records:
                record = json.loads(payload)
                matched = [k for k in config['keywords'] if normalized(k) in normalized(record['message'])]
                if matched:
                    count += db.execute("INSERT OR IGNORE INTO mentions(policy_id,message_id,room,keywords,record,at) VALUES(?,?,?,?,?,?)",
                                        (policy_id, message_id, config['room'], json.dumps(matched, ensure_ascii=False), payload, time.time())).rowcount
            if records:
                db.execute("UPDATE policies SET cursor=? WHERE id=?", (records[-1][0], policy_id))
            return count

    def perform(self, job, runner=None):
        config = json.loads(job['config'])
        try:
            result = (runner or self.run_collector)(config['room'])
            result['new_mentions'] = self.scan_mentions(job['policy_id'], config)
            with self.db() as db:
                db.execute("UPDATE jobs SET status='ok',finished=?,result=? WHERE id=?", (time.time(), json.dumps(result, ensure_ascii=False), job['id']))
        except Exception as exc:
            with self.db() as db:
                db.execute("UPDATE jobs SET status='error',finished=?,error=? WHERE id=?", (time.time(), str(exc)[:1500], job['id']))

    def run_collector(self, room):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('collector_demo.py')), 'run',
                                 '--room', room, '--output', str(self.folder)], stdin=subprocess.DEVNULL,
                                capture_output=True, encoding='utf-8', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            body = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(f"수집기 비정상 종료: {result.returncode}")
        if result.returncode or body.get('status') != 'ok':
            raise RuntimeError(body.get('error', '수집 실패'))
        return body

    def snapshot(self):
        with self.db() as db:
            jobs = [dict(r) for r in db.execute("SELECT id,policy_id,status,created,started,finished,result,error FROM jobs ORDER BY created DESC LIMIT 60")]
            mentions = [dict(r) for r in db.execute("SELECT * FROM mentions ORDER BY id DESC LIMIT 100")]
            audit = [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 60")]
        for job in jobs:
            job['result'] = json.loads(job['result']) if job['result'] else None
        for mention in mentions:
            mention['keywords'] = json.loads(mention['keywords'])
            mention['record'] = json.loads(mention['record'])
        with self.store.connect() as db:
            history = [{'room': r[0], 'messages': r[1]} for r in db.execute('SELECT room,count(*) FROM messages GROUP BY room')]
        return {"policies": self.list_policies(), "jobs": jobs, "mentions": mentions,
                "audit": audit, "history": history, "scheduler_enabled": self.scheduler()}
