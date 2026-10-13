from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption import chatgpt, conversations, prompt_versions
from caption.chat_files import snapshot_files
from caption.chat_tab import ChatPanel


class ChatFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in (("CONVERSATIONS_DIR", self.root / "conversations"),
                            ("SNAPSHOT_DIR", self.root / "snapshots")):
            patcher = patch.object(conversations, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = conversations.ConversationStore(self.root / "test.sqlite3")
        self.conversation = self.store.create("Test")
        self.panel = panel = object.__new__(ChatPanel)
        panel._busy = panel._file_importing = False
        panel._selected_image = None
        panel._selected_files = ()
        panel._file_generation = 0
        panel._store = self.store
        panel._get_conversation_id = Mock(return_value=self.conversation.id)
        panel._selected_chrome_tab_key = Mock(return_value="chrome-tab")
        panel.prompt_var = Mock(get=Mock(return_value="prompt"))
        panel.settings = SimpleNamespace(chat_prompt="")
        panel.attach_var = Mock(get=Mock(return_value=False))
        panel.history_var = Mock(get=Mock(return_value=False))
        panel.screenshot_var = Mock(get=Mock(return_value=False))
        panel._capture_screen = Mock()
        panel._new_conversation = False
        panel._power = Mock(return_value=None)
        panel._tab_results = queue.Queue()
        panel._run_chatgpt = Mock(return_value=True)
        panel._set_pending = Mock()
        panel._requests, panel._messages = {}, []
        panel._render = panel.refresh_state = panel._refresh_files = Mock()
        for patcher in (patch.object(prompt_versions, "resolve", return_value="prompt"),
                        patch.object(prompt_versions, "path_for", return_value=self.root / "prompt.txt"),
                        patch.object(chatgpt, "build_prompt", return_value="Prompt")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def source(self, name="notes.txt", content=b"original"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def attach(self, *sources):
        files = snapshot_files(self.conversation.folder, sources)
        self.panel._selected_files = files
        return files

    def test_snapshots_preserve_originals_and_survive_source_deletion(self):
        first, second = self.source("one.txt"), self.source("two.pdf", b"PDF bytes")
        files = self.attach(first, second)
        first.write_bytes(b"edited after import")
        second.unlink()
        self.assertEqual([path.read_bytes() for path in files], [b"original", b"PDF bytes"])
        self.assertEqual(first.read_bytes(), b"edited after import")
        self.assertTrue(all(path.is_relative_to(self.conversation.folder / "attachments") for path in files))

    def test_duplicate_names_and_builtin_context_names_get_distinct_upload_names(self):
        files = self.attach(self.source("a/report.pdf"), self.source("b/report.pdf"),
                            self.source("c/REPORT.pdf"), self.source("transcript.txt"))
        names = [path.name.casefold() for path in files]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("transcript.txt", names)
        more = snapshot_files(self.conversation.folder, [self.source("d/report.pdf")],
                              existing_names=[path.name for path in files])
        self.assertNotIn(more[0].name.casefold(), names)

    def test_failed_import_rolls_back_only_its_own_copies(self):
        source = self.source()
        previous = self.attach(source)[0]
        with self.assertRaises(OSError):
            snapshot_files(self.conversation.folder, [source, self.root / "missing.txt"])
        self.assertEqual(source.read_bytes(), b"original")
        self.assertTrue(previous.is_file())
        self.assertEqual(list((self.conversation.folder / "attachments").iterdir()), [previous.parent])

    def test_partial_copy_is_removed_if_copying_fails(self):
        def fail(source, destination):
            destination.write_bytes(b"partial")
            raise OSError("disk full")

        with patch("caption.chat_files.shutil.copy2", side_effect=fail), self.assertRaises(OSError):
            self.attach(self.source())
        self.assertEqual(list((self.conversation.folder / "attachments").iterdir()), [])

    def test_sends_multiple_files_and_persists_names_with_each_user_message(self):
        files = self.attach(self.source("notes.txt"), self.source("exercise.pdf"))
        self.assertTrue(self.panel._send_message("Explain these files"))
        request = next(iter(self.panel._requests.values()))
        self.assertEqual(request.files, files)
        with patch.object(chatgpt, "ask", return_value="Answer") as ask:
            self.panel._active_job(Mock(), threading.Event())
        self.assertEqual(ask.call_args.args[1], list(files))
        message = self.store.messages(self.conversation.id)[0]
        self.assertEqual(message.files, tuple(str(path.relative_to(self.conversation.folder)) for path in files))
        self.assertIn("exercise.pdf", self.panel._messages[0][2])
        self.assertIn("Attached files: notes.txt, exercise.pdf", self.store.chat_context_path(self.conversation.id).read_text(encoding="utf-8"))

    def test_files_are_combined_with_transcript_history_and_image(self):
        files = self.attach(self.source())
        transcript, image = self.source("transcript.txt"), self.source("photo.png")
        self.store.add_message(self.conversation.id, "user", "You", "Previous question")
        self.panel.attach_var.get.return_value = True
        self.panel.history_var.get.return_value = True
        self.panel._get_session_file = Mock(return_value=SimpleNamespace(
            has_text=True, snapshot=Mock(return_value=transcript), source="Test", started=None))
        self.panel._max_lines = Mock(return_value=50)
        with patch("caption.chat_tab.screenshot.thumbnail_data_uri", return_value="data:image/png;base64,"):
            self.assertTrue(self.panel._send_message("Question", image, reuse_image=True))
        with patch.object(chatgpt, "ask", return_value="Answer") as ask:
            self.panel._active_job(Mock(), threading.Event())
        attachments = ask.call_args.args[1]
        self.assertEqual(attachments[0], transcript)
        self.assertEqual(attachments[1].suffix, ".md")
        self.assertEqual(attachments[2:], [image, *files])

    def test_successful_retry_reuses_old_files_not_new_draft_and_rebuilds_history(self):
        original = self.attach(self.source("first.txt"))
        self.panel._send_message("Original question")
        key = next(iter(self.panel._requests))
        self.panel._requests[key].failed = False
        self.attach(self.source("new-draft.txt"))
        self.panel.history_var.get.return_value = True
        self.panel.retry(key)
        latest = next(reversed(self.panel._requests.values()))
        self.assertEqual(latest.files, original)
        with patch.object(chatgpt, "ask", return_value="Answer") as ask:
            latest.job(Mock(), threading.Event())
        attachments = ask.call_args.args[1]
        self.assertEqual(attachments[-1:], list(original))
        self.assertEqual(attachments[0].suffix, ".md")
        self.assertIn("Original question", attachments[0].read_text(encoding="utf-8"))
        self.assertEqual(self.panel._selected_files[0].name, "new-draft.txt")

    def test_failed_retry_keeps_the_exact_job_and_attachments(self):
        files = self.attach(self.source())
        self.panel._send_message("Question")
        key = next(iter(self.panel._requests))
        request = self.panel._requests[key]
        request.failed = True
        old_job = request.job
        self.attach(self.source("new.txt"))
        self.panel.retry(key)
        self.assertIs(self.panel._active_job, old_job)
        self.assertEqual(len(self.store.messages(self.conversation.id)), 1)
        with patch.object(chatgpt, "ask", return_value="Answer") as ask:
            self.panel._active_job(Mock(), threading.Event())
        self.assertEqual(ask.call_args.args[1], list(files))

    def test_reopening_after_rename_restores_files_and_retry(self):
        files = self.attach(self.source())
        self.panel._send_message("Question")
        renamed = self.store.rename(self.conversation.id, "Renamed conversation")
        self.panel._clear_image = self.panel._clear_files = Mock()
        self.panel.load_conversation()
        request = next(iter(self.panel._requests.values()))
        self.assertEqual(request.files, (renamed.folder / files[0].relative_to(self.conversation.folder),))
        self.assertTrue(request.files[0].is_file())
        self.panel.retry(next(iter(self.panel._requests)))
        with patch.object(chatgpt, "ask", return_value="Answer") as ask:
            self.panel._active_job(Mock(), threading.Event())
        self.assertEqual(ask.call_args.args[1], list(request.files))

    def test_missing_copy_prevents_sending_instead_of_silently_omitting_it(self):
        files = self.attach(self.source())
        files[0].unlink()
        with patch("caption.chat_tab.messagebox.showerror") as notice:
            self.assertFalse(self.panel._send_message("Question"))
        notice.assert_called_once()
        self.panel._run_chatgpt.assert_not_called()
        self.assertEqual(self.store.messages(self.conversation.id), [])

    def test_file_only_send_uses_default_message_and_clears_only_after_acceptance(self):
        self.attach(self.source())
        self.panel.entry = Mock(get=Mock(return_value=""))
        self.panel._clear_image = Mock()
        self.panel._clear_files = Mock()
        self.panel._send_message = Mock(return_value=False)
        self.panel.send()
        self.panel._clear_files.assert_not_called()
        self.panel._send_message.assert_called_with("Hãy phân tích nội dung các file đính kèm này.")
        self.panel._send_message.return_value = True
        self.panel.send()
        self.panel._clear_files.assert_called_once()

    def test_import_is_background_and_callback_never_attaches_to_another_conversation(self):
        entered, release = threading.Event(), threading.Event()
        imported = self.source()

        def copy(*args):
            entered.set()
            release.wait(2)
            return (imported,)

        try:
            with patch("caption.chat_tab.filedialog.askopenfilenames", return_value=(str(imported),)), \
                 patch("caption.chat_tab.snapshot_files", side_effect=copy):
                self.panel._choose_files()
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.panel._file_importing)
                self.panel._get_conversation_id.return_value = "another conversation"
                self.panel._file_generation += 1
                release.set()
                callback = self.panel._tab_results.get(timeout=2)
                callback()
            self.assertEqual(self.panel._selected_files, ())
        finally:
            release.set()

    def test_send_is_disabled_during_file_import_and_labels_escape_html(self):
        self.panel._file_importing = True
        self.panel.entry = Mock(get=Mock(return_value="Question"))
        self.panel._send_message = Mock()
        self.panel.send()
        self.panel._send_message.assert_not_called()
        html = self.panel._files_html([Path("<unsafe>.pdf")])
        self.assertNotIn("<unsafe>", html)
        self.assertIn("&lt;unsafe&gt;", html)

    def test_remove_and_clear_only_detach_without_deleting_original_or_saved_copies(self):
        sources = [self.source(f"file{i}.txt") for i in range(3)]
        files = self.attach(*sources)
        self.panel.files_list = Mock(curselection=Mock(return_value=(1,)))
        self.panel._remove_file()
        self.assertEqual(self.panel._selected_files, (files[0], files[2]))
        self.panel._clear_files()
        self.assertEqual(self.panel._selected_files, ())
        self.assertEqual(self.panel._file_generation, 1)
        self.assertTrue(all(path.exists() for path in [*sources, *files]))

    def test_import_success_and_error_callbacks_finish_on_ui_thread(self):
        file = self.source()
        self.panel._file_importing = True
        self.panel._files_imported(self.conversation.id, 0, (file,), "")
        self.assertFalse(self.panel._file_importing)
        self.assertEqual(self.panel._selected_files, (file,))
        self.panel._file_importing = True
        with patch("caption.chat_tab.messagebox.showerror") as notice:
            self.panel._files_imported(self.conversation.id, 0, (), "disk full")
        notice.assert_called_once()
        self.assertFalse(self.panel._file_importing)
        self.assertEqual(self.panel._selected_files, (file,))

    def test_old_database_gets_attachment_table_without_changing_existing_messages(self):
        self.store.add_message(self.conversation.id, "user", "You", "Existing question")
        with self.store._connect() as db:
            db.execute("DROP TABLE chat_attachments")
        reopened = conversations.ConversationStore(self.store.db_path)
        messages = reopened.messages(self.conversation.id)
        self.assertEqual(messages[0].content, "Existing question")
        self.assertEqual(messages[0].files, ())
        files = self.attach(self.source())
        message_id = reopened.add_message(self.conversation.id, "user", "You", "New question", files=files)
        self.assertEqual(len(reopened.messages(self.conversation.id)[1].files), 1)
        with reopened._connect() as db:
            db.execute("DELETE FROM chat_messages WHERE id=?", (message_id,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
