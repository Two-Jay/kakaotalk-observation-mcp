import json
import sqlite3
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

from watcher import check, connect, new_start, parse_chat, pending


def chat(*bodies):
    return "2026년 11월 14일 토요일\n" + "\n".join(
        f"[친구] [오전 8:{i:02d}] {body}" for i, body in enumerate(bodies))


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = connect(self.base / "state.db")
        self.config = {"keywords": ["두기"], "rooms": [
            {"name": "방", "source": "file", "path": "chat.txt"}]}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def run_check(self, text):
        return check(self.config, self.base, self.db, reader=lambda room: text)

    def test_first_run_baselines_old_mentions(self):
        result = self.run_check(chat("두기 예전 메시지"))
        self.assertFalse(result["detected"])
        self.assertEqual(result["initialized_rooms"], ["방"])

    def test_new_mention_persists_until_ack_and_no_duplicate(self):
        self.run_check(chat("안녕"))
        text = chat("안녕", "두기 회의 와줘")
        result = self.run_check(text)
        self.assertTrue(result["detected"])
        event = result["events"][0]
        self.assertEqual(event["sender"], "친구")
        self.assertEqual(len(self.run_check(text)["events"]), 1)
        self.db.execute("UPDATE events SET acked=1 WHERE id=?", (event["id"],))
        self.db.commit()
        self.assertFalse(self.run_check(text)["detected"])

    def test_restart_keeps_pending(self):
        self.run_check(chat("안녕"))
        self.run_check(chat("안녕", "두기!"))
        self.db.close()
        self.db = connect(self.base / "state.db")
        self.assertEqual(len(pending(self.db)), 1)

    def test_multiline_keyword_body_only(self):
        records = parse_chat("[두기] [오전 9:00] 안녕\n[친구] [오전 9:01] 다음 줄\n두기 와줘")
        self.assertEqual(records[1]["message"], "다음 줄\n두기 와줘")
        self.run_check("[친구] [오전 8:00] 안녕")
        result = self.run_check("[친구] [오전 8:00] 안녕\n[두기] [오전 9:00] 안녕")
        self.assertFalse(result["detected"])

    def test_gap_is_error_and_does_not_advance(self):
        self.run_check(chat("처음"))
        result = self.run_check(chat("전혀 다른 내용", "두기"))
        self.assertEqual(result["status"], "partial")
        self.assertIn("coverage_gap", result["errors"][0]["error"])
        self.assertTrue(self.run_check(chat("처음", "두기"))["detected"])

    def test_sliding_snapshot_and_prepend(self):
        self.assertEqual(new_start(["a", "b", "c"], ["b", "c", "d"]), 2)
        self.assertEqual(new_start(["b", "c"], ["a", "b", "c", "d"]), 3)

    def test_ambiguous_tail_refused(self):
        with self.assertRaises(ValueError):
            new_start(["a"], ["a", "b", "a"])

    def test_bad_capture_preserves_checkpoint(self):
        self.run_check(chat("안녕"))
        self.assertEqual(self.run_check("")["status"], "partial")
        self.assertTrue(self.run_check(chat("안녕", "두기"))["detected"])

    def test_partial_room_failure_retains_other_events(self):
        self.run_check(chat("안녕"))
        self.config["rooms"].append({"name": "오류방", "source": "file", "path": "bad"})
        def reader(room):
            if room["name"] == "오류방":
                raise RuntimeError("창 없음")
            return chat("안녕", "두기")
        result = check(self.config, self.base, self.db, reader=reader)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["detected"])

    def test_changed_source_refused(self):
        self.run_check(chat("안녕"))
        self.config["rooms"][0]["path"] = "other.txt"
        result = self.run_check(chat("안녕", "두기"))
        self.assertEqual(result["status"], "partial")

    def test_concurrent_capture_is_serialized(self):
        second = connect(self.base / "state.db")
        second.execute("BEGIN IMMEDIATE")
        calls = []
        try:
            with self.assertRaises(sqlite3.OperationalError):
                check(self.config, self.base, self.db,
                      reader=lambda room: calls.append(room))
            self.assertEqual(calls, [])
        finally:
            second.rollback()
            second.close()

    def test_cli_detect_ack_and_invalid_ack(self):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        chat_path = self.base / "chat.txt"
        script = str(Path(__file__).with_name("watcher.py"))
        def cli(*args):
            completed = subprocess.run([sys.executable, script, "--config", str(config_path), *args],
                                       capture_output=True, encoding="utf-8", check=False)
            return completed.returncode, json.loads(completed.stdout)
        chat_path.write_text(chat("안녕"), encoding="utf-8")
        self.assertEqual(cli("check")[0], 0)
        chat_path.write_text(chat("안녕", "두기 회의 와줘"), encoding="utf-8")
        code, result = cli("check")
        self.assertEqual(code, 0)
        event_id = result["events"][0]["id"]
        self.assertEqual(cli("ack", event_id, "unknown")[0], 2)
        self.assertTrue(cli("pending")[1]["detected"])
        self.assertEqual(cli("ack", event_id)[0], 0)
        self.assertFalse(cli("check")[1]["detected"])

    def test_cli_bad_config_is_json_error(self):
        script = str(Path(__file__).with_name("watcher.py"))
        completed = subprocess.run([sys.executable, script, "--config", str(self.base / "missing"), "check"],
                                   capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["status"], "error")


if __name__ == "__main__":
    unittest.main()
