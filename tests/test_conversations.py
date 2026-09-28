from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption import conversations


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(conversations, "CONVERSATIONS_DIR", self.root / "conversations"),
            patch.object(conversations, "TRANSCRIPTS_DIR", self.root / "legacy"),
            patch.object(conversations, "PART_LIMIT", 180),
        ]
        for item in self.patches:
            item.start()
        self.store = conversations.ConversationStore(self.root / "conversations.sqlite3")

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_resume_split_snapshot_rename_and_chat(self) -> None:
        item = self.store.create("Microphone: Test")
        transcript = conversations.ConversationTranscript(item, self.store)
        transcript.begin_segment()
        transcript.write("first day", True)
        transcript.begin_segment()
        transcript.write("second day " * 30, True)

        self.assertGreaterEqual(len(transcript.parts()), 2)
        self.assertIn("first day", transcript.snapshot(0).read_text(encoding="utf-8"))
        self.assertIn("second day", transcript.snapshot(1).read_text(encoding="utf-8"))

        self.store.add_message(item.id, "user", "You", "What did we discuss?")
        self.store.add_message(item.id, "bot", "ChatGPT", "Two sessions.", kind="summary")
        renamed = self.store.rename(item.id, "Long-running project")

        self.assertTrue(renamed.folder.exists())
        self.assertFalse(item.folder.exists())
        self.assertEqual(self.store.latest_summary(item.id).content, "Two sessions.")
        self.assertTrue(self.store.chat_context_path(item.id).exists())

    def test_legacy_import_is_non_destructive_and_idempotent(self) -> None:
        conversations.TRANSCRIPTS_DIR.mkdir(parents=True)
        old = conversations.TRANSCRIPTS_DIR / "old.txt"
        old.write_text(
            "# Source: Windows Live Captions\n# Started: 2026-09-01 10:00:00\n\n[10:00:01] hello\n",
            encoding="utf-8",
        )
        self.assertEqual(self.store.import_legacy(), 1)
        self.assertEqual(self.store.import_legacy(), 0)
        self.assertTrue(old.exists())
        self.assertIn("hello", conversations.ConversationTranscript(self.store.list()[0], self.store).read_text())


if __name__ == "__main__":
    unittest.main()
