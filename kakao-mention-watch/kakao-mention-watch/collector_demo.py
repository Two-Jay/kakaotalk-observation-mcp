"""One-shot JSON interface for an agent-independent Windows collector demo."""
import argparse
import json
from pathlib import Path
import sys

from collector_store import TranscriptStore


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["inspect", "search", "open", "collect", "run", "status"])
    p.add_argument("--room", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    store = TranscriptStore(args.output)
    try:
        if args.command == "status":
            result = {"status": "ok", "room": args.room, **store.status(args.room)}
        else:
            from windows_collector import WindowsCollector, desktop_lock
            with desktop_lock():
                source = WindowsCollector(args.room, args.output)
                if args.command == "inspect":
                    result = source.inspect()
                elif args.command == "search":
                    result = source.search_preview()
                elif args.command == "open":
                    result = source.open_room()
                else:
                    if args.command == "run":
                        source.open_room()
                    text = source.read()
                    pages = 0
                    while True:
                        try:
                            store.check_overlap(args.room, text)
                            break
                        except ValueError as exc:
                            if not str(exc).startswith("coverage_gap:") or pages >= 32:
                                raise
                            text = source.read(history_pageups=8)
                            pages += 8
                    result = store.ingest(args.room, text)
                    result["backtrack_pages"] = pages
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        code = str(exc).split(":", 1)[0] if ":" in str(exc) else type(exc).__name__
        store.failure(args.room, code)
        print(json.dumps({"status": "error", "error": str(exc), "error_code": code}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
