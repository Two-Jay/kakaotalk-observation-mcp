"""KakaoTalk mention detector. stdout is one JSON object; no network or sending."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MESSAGE = re.compile(r"^\[([^\]\r\n]+)\] \[([^\]\r\n]+)\] ?(.*)$")
DATE = re.compile(r"^[-\s]*(\d{4}년\s*\d{1,2}월\s*\d{1,2}일.*?)[-\s]*$")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_chat(text):
    """Parse PC clipboard [sender] [time] records; keep multiline bodies together."""
    records, current, day = [], None, None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        date = DATE.match(line)
        match = MESSAGE.match(line)
        if date or match:
            if current:
                records.append(current)
                current = None
            if date:
                day = date.group(1).strip()
            else:
                current = {"sender": match[1], "time_label": match[2],
                           "date_label": day, "message": match[3]}
        elif current:
            current["message"] += "\n" + line
    if current:
        records.append(current)
    if not records:
        raise ValueError("지원되는 [보낸사람] [시간] 메시지가 없습니다. 진단 후 복사 형식을 확인하세요.")
    for record in records:
        record["message"] = record["message"].rstrip("\n")
        record["hash"] = digest(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return records


def new_start(previous, current):
    """Locate a unique previous tail. Refuse to guess when coverage is lost."""
    if previous == current:
        return len(current)
    for size in range(min(8, len(previous)), min(2, len(previous)) - 1, -1):
        anchor = previous[-size:]
        positions = [i + size for i in range(len(current) - size + 1)
                     if current[i:i + size] == anchor]
        if len(positions) == 1:
            return positions[0]
        if positions:
            break
    raise ValueError("coverage_gap: 이전 마지막 메시지와 연결되지 않거나 중복되어 신규 범위를 확정할 수 없습니다.")


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    keywords = config.get("keywords")
    if not isinstance(keywords, list) or not keywords or any(
            not isinstance(k, str) or not k.strip() for k in keywords):
        raise ValueError("keywords에는 비어 있지 않은 문자열을 넣으세요.")
    rooms = config.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise ValueError("rooms를 하나 이상 지정하세요.")
    names = set()
    for room in rooms:
        name = room.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("방 이름은 비어 있지 않고 고유해야 합니다.")
        names.add(name)
        if room.get("source") not in ("window", "file"):
            raise ValueError("source는 window 또는 file이어야 합니다.")
        if room["source"] == "window":
            if not room.get("window_title") or not room.get("history_class"):
                raise ValueError("window_title, history_class를 지정하세요.")
            if "edit" in room["history_class"].lower():
                raise ValueError("입력창 Edit/RichEdit 클래스는 수집 대상으로 사용할 수 없습니다.")
        elif not room.get("path"):
            raise ValueError("file 소스에는 path가 필요합니다.")
    return config


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=1)
    db.execute("CREATE TABLE IF NOT EXISTS snapshots (room TEXT PRIMARY KEY, source TEXT, hashes TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT, acked INTEGER DEFAULT 0)")
    db.commit()
    return db


def check(config, base, db, reader=None):
    errors, initialized = [], []
    db.execute("BEGIN IMMEDIATE")  # Also serializes capture/clipboard access between invocations.
    try:
        for room in config["rooms"]:
            try:
                text = (reader(room) if reader else read_room(room, base))
                records = parse_chat(text)
                hashes = [r["hash"] for r in records]
                source = digest(json.dumps(room, sort_keys=True, ensure_ascii=False))
                old = db.execute("SELECT source, hashes FROM snapshots WHERE room=?", (room["name"],)).fetchone()
                start = 0
                if old is None:
                    start = len(records)
                    initialized.append(room["name"])
                elif old[0] != source:
                    raise ValueError("source_changed: 수집 대상 설정이 달라졌습니다. 방 이름을 새로 지정하세요.")
                else:
                    start = new_start(json.loads(old[1]), hashes)
                for index in range(start, len(records)):
                    record = records[index]
                    body = unicodedata.normalize("NFC", record["message"])
                    compare = body if config.get("case_sensitive", False) else body.casefold()
                    matched = [k for k in config["keywords"] if
                               (unicodedata.normalize("NFC", k) if config.get("case_sensitive", False)
                                else unicodedata.normalize("NFC", k).casefold()) in compare]
                    if not matched:
                        continue
                    # Preceding records distinguish repeated identical messages where possible.
                    event_id = digest(room["name"] + json.dumps(hashes[max(0, index-8):index+1]))
                    payload = {"id": event_id, "room": room["name"], "keywords": matched,
                               "sender": record["sender"], "message": record["message"],
                               "date_label": record["date_label"], "time_label": record["time_label"],
                               "observed_at": now(),
                               "context": [{k: v for k, v in r.items() if k != "hash"}
                                           for r in records[max(0, index-2):index]]}
                    db.execute("INSERT OR IGNORE INTO events(id,payload) VALUES (?,?)",
                               (event_id, json.dumps(payload, ensure_ascii=False)))
                db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)",
                           (room["name"], source, json.dumps(hashes[-2000:])))
            except Exception as exc:
                errors.append({"room": room["name"], "error": str(exc)})
                # Preserve old anchor on failure: do not silently skip missing history.
        db.commit()
    except BaseException:
        db.rollback()
        raise
    events = pending(db)
    return {"status": "partial" if errors else "ok", "detected": bool(events),
            "events": events, "initialized_rooms": initialized, "errors": errors,
            "checked_at": now()}


def pending(db):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT payload FROM events WHERE acked=0 ORDER BY rowid")]


def read_room(room, base):
    if room["source"] == "file":
        return (base / room["path"]).read_text(encoding="utf-8-sig")
    from windows_source import capture
    return capture(room)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Read rooms; return all unacknowledged mentions")
    commands.add_parser("pending", help="Read queue without touching KakaoTalk")
    ack = commands.add_parser("ack", help="Acknowledge only IDs successfully handled by agent")
    ack.add_argument("ids", nargs="+")
    commands.add_parser("inspect", help="List KakaoTalk windows and control classes; no text copy")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = None
    try:
        if args.command == "inspect":
            from windows_source import inspect_windows
            result = {"status": "ok", "windows": inspect_windows()}
        else:
            path = args.config.resolve()
            config = load_config(path)
            db = connect(path.parent / config.get("state_file", "data/state.sqlite3"))
            if args.command == "check":
                result = check(config, path.parent, db)
            elif args.command == "pending":
                events = pending(db)
                result = {"status": "ok", "detected": bool(events), "events": events}
            else:
                db.execute("BEGIN IMMEDIATE")
                known = {r[0] for r in db.execute("SELECT id FROM events")}
                if any(i not in known for i in args.ids):
                    raise ValueError("존재하지 않는 이벤트 ID입니다.")
                db.executemany("UPDATE events SET acked=1 WHERE id=?", [(i,) for i in args.ids])
                db.commit()
                result = {"status": "ok", "acknowledged": args.ids}
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] != "ok" else 0
    except Exception as exc:
        if db:
            db.rollback()
        print(json.dumps({"status": "error", "detected": False, "events": [], "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if db:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
