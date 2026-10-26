from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from caption import conversations
from caption.gui import App
from caption.transcript_export import last_lines


TEXT = "# Source: Test\n# Started: 2026-10-25 10:00:00\n\n[10:00:01] First\n[10:00:02] Second\n[10:00:03] Third\n"


class TranscriptExcerptTests(unittest.TestCase):
    def test_last_n_captions_keep_headers_but_do_not_count_them(self):
        result = last_lines(TEXT, 2)
        self.assertNotIn("[10:00:01]", result)
        self.assertIn("[10:00:02] Second", result)
        self.assertIn("[10:00:03] Third", result)
        self.assertTrue(result.startswith("# Source: Test\n# Started:"))
        self.assertIn("the last 2 of 3 caption lines", result)

    def test_zero_and_larger_than_total_export_everything(self):
        for count in (0, 3, 10000):
            self.assertEqual(last_lines(TEXT, count), TEXT.strip())

    def test_continuations_and_split_file_metadata_are_not_extra_captions(self):
        text = TEXT + "continued third line\n\n# Source: Test\n# Continued transcript part\n\n[10:00:04] Fourth\ncontinued fourth line"
        result = last_lines(text, 2)
        self.assertNotIn("[10:00:02]", result)
        self.assertIn("[10:00:03] Third\ncontinued third line", result)
        self.assertIn("[10:00:04] Fourth\ncontinued fourth line", result)

    def test_plain_text_fallback_and_invalid_count(self):
        self.assertEqual(last_lines("First\nSecond\nThird", 2), "Second\nThird")
        self.assertEqual(last_lines("", 3), "")
        with self.assertRaises(ValueError):
            last_lines(TEXT, -1)


class TranscriptDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.target = self.folder / "download.txt"
        self.app = app = object.__new__(App)
        app.root = Mock()
        app.session_file = Mock()
        app.session_file.read_text.return_value = TEXT
        app.session_file.parts.return_value = []
        app.transcript = Mock()
        app.status_var = Mock()
        for name, value in (("simpledialog.askinteger", 2),
                            ("filedialog.asksaveasfilename", str(self.target)),
                            ("messagebox.showerror", None), ("messagebox.showinfo", None)):
            patcher = patch("caption.gui." + name, return_value=value)
            mock = patcher.start()
            setattr(self, name.rsplit(".", 1)[-1], mock)
            self.addCleanup(patcher.stop)

    def test_download_saved_transcript_opens_count_setting_then_saves_excerpt(self):
        self.app.download_transcript()
        self.app.transcript.get.assert_not_called()
        self.askinteger.assert_called_once()
        self.assertEqual(self.askinteger.call_args.kwargs["minvalue"], 0)
        self.assertEqual(self.askinteger.call_args.kwargs["initialvalue"], 0)
        self.assertEqual(self.target.read_text(encoding="utf-8"), last_lines(TEXT, 2) + "\n")
        self.app.status_var.set.assert_called_once()
        self.showerror.assert_not_called()

    def test_zero_downloads_full_transcript_and_remembers_last_choice(self):
        self.askinteger.return_value = 0
        self.app.download_transcript()
        self.assertEqual(self.target.read_text(encoding="utf-8"), TEXT)
        self.askinteger.return_value = 50
        self.app.download_transcript()
        self.app.download_transcript()
        self.assertEqual(self.askinteger.call_args.kwargs["initialvalue"], 50)

    def test_cancel_setting_or_save_dialog_does_not_write_any_file(self):
        self.askinteger.return_value = None
        self.app.download_transcript()
        self.asksaveasfilename.assert_not_called()
        self.assertFalse(self.target.exists())
        self.askinteger.return_value = 1
        self.asksaveasfilename.return_value = ""
        self.app.download_transcript()
        self.assertFalse(self.target.exists())
        self.app.status_var.set.assert_not_called()

    def test_empty_or_metadata_only_transcript_shows_notice(self):
        for text in ("", "  \n", "# Source: Test\n# Started: today\n"):
            self.app.session_file.read_text.return_value = text
            self.app.download_transcript()
        self.assertEqual(self.showinfo.call_count, 3)
        self.askinteger.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_read_or_write_failure_shows_error_without_success_status(self):
        self.app.session_file.read_text.side_effect = OSError("cannot read")
        self.app.download_transcript()
        self.askinteger.assert_not_called()
        self.app.session_file.read_text.side_effect = None
        with patch("builtins.open", side_effect=PermissionError("cannot write")):
            self.app.download_transcript()
        self.assertEqual(self.showerror.call_count, 2)
        self.app.status_var.set.assert_not_called()

    def test_without_session_uses_visible_transcript(self):
        self.app.session_file = None
        self.app.transcript.get.return_value = "First\nSecond\nThird"
        self.app.download_transcript()
        self.assertEqual(self.target.read_text(encoding="utf-8"), "Second\nThird\n")

    def test_cannot_overwrite_original_recording_with_excerpt(self):
        self.target.write_text(TEXT, encoding="utf-8")
        self.app.session_file.parts.return_value = [self.target]
        self.app.download_transcript()
        self.assertEqual(self.target.read_text(encoding="utf-8"), TEXT)
        self.showerror.assert_called_once()
        self.app.status_var.set.assert_not_called()

    def test_download_merges_all_saved_parts_even_after_display_is_cleared(self):
        with patch.object(conversations, "CONVERSATIONS_DIR", self.folder / "conversations"):
            store = conversations.ConversationStore(self.folder / "test.sqlite3")
            item = store.create("Test")
            transcript = conversations.ConversationTranscript(item, store)
            transcript.begin_segment()
            with patch.object(conversations, "PART_LIMIT", 1):
                for word in ("FIRST", "SECOND", "THIRD"):
                    transcript.write(word, True)
            transcript.close()
            originals = {path: path.read_bytes() for path in transcript.parts()}
            self.assertGreater(len(originals), 1)
            self.app.session_file = transcript
            self.app.transcript.get.return_value = ""
            self.app.download_transcript()
            result = self.target.read_text(encoding="utf-8")
            self.assertNotIn("] FIRST", result)
            self.assertIn("] SECOND", result)
            self.assertIn("] THIRD", result)
            self.assertEqual(originals, {path: path.read_bytes() for path in transcript.parts()})


if __name__ == "__main__":
    unittest.main()
