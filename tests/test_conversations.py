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

        self.store.add_message(item.id, "user", "You", "Summarize this conversation", kind="summary")
        self.store.add_message(item.id, "bot", "ChatGPT", "Two sessions.", kind="summary")
        self.store.add_message(item.id, "bot", "ChatGPT", "A newer summary.", kind="summary")
        transcript.close()  # Renaming requires the active capture to be paused.
        renamed = self.store.rename(item.id, "Long-running project")

        self.assertTrue(renamed.folder.exists())
        self.assertFalse(item.folder.exists())
        self.assertEqual(self.store.latest_summary(item.id).content, "A newer summary.")
        self.assertEqual(
            [message.content for message in self.store.summaries(item.id)],
            ["Two sessions.", "A newer summary."],
        )
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

    def test_response_position_migration_preserves_old_content_and_source_ids(self):
        item = self.store.create('Test')
        self.store.add_message(item.id, 'bot', 'ChatGPT', 'Old answer',
                               response_conversation_url='https://chatgpt.com/c/old', response_message_id='legacy-id')
        with self.store._connect() as db:
            db.execute('ALTER TABLE chat_messages DROP COLUMN response_position')
        reopened = conversations.ConversationStore(self.store.db_path)
        old = reopened.messages(item.id)[0]
        self.assertEqual(old.content, 'Old answer')
        self.assertEqual(old.response_conversation_url, 'https://chatgpt.com/c/old')
        self.assertEqual(old.response_message_id, 'legacy-id')
        self.assertIsNone(old.response_position)
        link = 'https://chatgpt.com/g/g-p-project/c/new?keep=this#fragment'
        reopened.add_message(item.id, 'bot', 'ChatGPT', 'New answer', response_conversation_url=link, response_position=4)
        latest = conversations.ConversationStore(self.store.db_path).messages(item.id)[-1]
        self.assertEqual(latest.response_conversation_url, link)
        self.assertEqual(latest.response_position, 4)


if __name__ == "__main__":
    unittest.main()
