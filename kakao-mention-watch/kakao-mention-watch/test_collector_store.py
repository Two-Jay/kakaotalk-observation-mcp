import tempfile
import unittest
from pathlib import Path

from collector_store import TranscriptStore


def chat(*bodies):
    return "2026년 11월 14일 토요일\n" + "\n".join(
        f"[테스트] [오후 9:{i:02d}] {body}" for i, body in enumerate(bodies))


class TranscriptTests(unittest.TestCase):
    def test_restart_incremental_repeat_and_export_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TranscriptStore(folder)
            first = store.ingest("테스트방", chat("첫 메시지", "두기"))
            self.assertEqual(first["new_messages"], 2)
            path = Path(first["text_file"])
            self.assertIn("두기", path.read_text(encoding="utf-8"))
            path.write_text("incomplete export", encoding="utf-8")
            store = TranscriptStore(folder)
            self.assertEqual(store.ingest("테스트방", chat("첫 메시지", "두기"))["new_messages"], 0)
            result = store.ingest("테스트방", chat("첫 메시지", "두기", "새 메시지"))
            self.assertEqual(result["new_messages"], 1)
            self.assertEqual(result["total_messages"], 3)
            self.assertEqual(path.read_text(encoding="utf-8").count("첫 메시지"), 1)

    def test_gap_and_bad_capture_preserve_checkpoint_and_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TranscriptStore(folder)
            result = store.ingest("방", chat("A", "B"))
            original = Path(result["text_file"]).read_bytes()
            for text in (chat("X", "Y"), "", chat("A", "B") + "\n" + chat("A", "B")):
                with self.assertRaises(ValueError):
                    store.ingest("방", text)
                self.assertEqual(store.status("방")["total_messages"], 2)
                self.assertEqual(Path(result["text_file"]).read_bytes(), original)
            self.assertEqual(store.ingest("방", chat("A", "B", "C"))["new_messages"], 1)


if __name__ == "__main__":
    unittest.main()
