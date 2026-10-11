from __future__ import annotations

import queue
import threading
import unittest
from unittest.mock import Mock, patch

from caption import vocab
from caption.settings import Settings
from caption.vocab_tab import VocabPanel, VocabRequest, VocabTranslation


class VocabFlowTests(unittest.TestCase):
    def panel(self) -> VocabPanel:
        panel = object.__new__(VocabPanel)
        panel.settings = Settings(voca_api_key="test-key", voca_collection_id="collection")
        panel._busy = panel._chatgpt_busy = panel._saving_to_voca = False
        panel._voca_queue = None
        panel._voca_deferred = 0
        panel._closed = False
        panel._new_conversation = False
        panel._messages = []
        panel._requests = {"request-1": VocabRequest("example", active=True)}
        panel._active_request_id = "request-1"
        panel._active_job = Mock()
        panel._request_sequence = 1
        panel._results = queue.Queue()
        panel.refresh_state = Mock()
        panel._schedule_render = Mock()
        panel._render = Mock()
        panel.after_idle = Mock()
        self.addCleanup(self.close_panel, panel)
        return panel

    @staticmethod
    def close_panel(panel):
        panel.close()
        if panel._voca_queue:
            panel._voca_queue._thread.join(3)

    def finish_upload(self, panel):
        while True:
            status, task, words = panel._voca_queue.results.get(timeout=2)
            if status == "saving":
                panel._update_voca_message(words, task.message)
            else:
                panel._on_voca_saved(words, task.message)
                return

    def translation(self, api_key: str = "test-key") -> VocabTranslation:
        return VocabTranslation(
            [vocab.NewWord("example", translation="an illustration")],
            api_key, "collection", "Caption source",
        )

    def test_chatgpt_job_returns_translation_without_sending_to_voca(self) -> None:
        panel = self.panel()
        panel._get_session_file = Mock(return_value=None)
        panel._power = Mock(return_value=None)
        panel._run_chatgpt = Mock(return_value=True)
        with (
            patch("caption.vocab_tab.vocab.build_prompt", return_value="Translate example"),
            patch("caption.vocab_tab.chatgpt.ask", return_value='[{"word":"example","translation":"an illustration"}]'),
            patch("caption.vocab_tab._save_to_voca") as save,
        ):
            self.assertTrue(panel._send_text("example"))
            job = panel._run_chatgpt.call_args.args[0]
            translation = job(Mock(), threading.Event())

        save.assert_not_called()
        self.assertEqual(translation.words[0].translation, "an illustration")
        self.assertEqual(translation.collection_id, "collection")

    def test_response_is_rendered_before_upload_and_updated_in_place(self) -> None:
        panel = self.panel()
        translation = self.translation()
        panel._on_done(translation)
        response = next(item for item in panel._messages if item[0] == "bot")
        self.assertIn("an illustration", response[2])
        self.assertIn("Queued for Voca", response[2])
        panel._render.assert_called()
        self.assertFalse(panel._busy)
        self.assertFalse(panel._requests["request-1"].active)
        # The upload has not started until the UI's idle callback runs.
        self.assertTrue(panel._results.empty())
        entered, release = threading.Event(), threading.Event()

        def save(_key, _collection, words, _source):
            entered.set()
            if not release.wait(2):
                raise AssertionError("Test upload was not released")
            words[0].voca_status = "created"

        with patch("caption.vocab_tab._save_to_voca", side_effect=save):
            panel.after_idle.call_args.args[0]()
            try:
                self.assertTrue(entered.wait(2))
                self.assertIs(next(m for m in panel._messages if m[0] == "bot"), response)
                self.assertEqual(translation.words[0].voca_status, "")
                # Saving must not keep New words locked after ChatGPT is done.
                panel.set_busy(False)
                self.assertFalse(panel._busy)
            finally:
                release.set()
            self.finish_upload(panel)

        responses = [m for m in panel._messages if m[0] == "bot"]
        self.assertEqual(len(responses), 1)
        self.assertIn("an illustration", responses[0][2])
        self.assertIn("Saved to Voca", responses[0][2])
        self.assertIn("1 saved to Voca", responses[0][1])
        self.assertFalse(panel._busy)

    def test_voca_failure_preserves_response_and_shared_chat_busy_state(self) -> None:
        panel = self.panel()
        panel._on_done(self.translation())
        panel.set_busy(True)  # Another panel has started a ChatGPT request.
        with (
            patch("caption.vocab_tab._save_to_voca", side_effect=RuntimeError("upload failed")),
            patch("caption.voca_queue.log.warning"),
        ):
            panel.after_idle.call_args.args[0]()
            self.finish_upload(panel)

        responses = [m for m in panel._messages if m[0] == "bot"]
        self.assertEqual(len(responses), 1)
        self.assertIn("an illustration", responses[0][2])
        self.assertIn("Not saved", responses[0][2])
        self.assertFalse(panel._saving_to_voca)
        self.assertTrue(panel._busy)
        panel.set_busy(False)
        self.assertFalse(panel._busy)

    def test_can_send_next_words_while_voca_is_saving_and_completion_keeps_stream(self):
        panel = self.panel()
        panel._get_session_file = Mock(return_value=None)
        panel._power = Mock(return_value=None)
        panel._run_chatgpt = Mock(return_value=True)
        panel._on_done(self.translation())
        entered, release = threading.Event(), threading.Event()

        def save(_key, _collection, words, _source):
            entered.set()
            release.wait(2)
            words[0].voca_status = "created"

        with patch("caption.vocab_tab._save_to_voca", side_effect=save):
            panel.after_idle.call_args.args[0]()
            try:
                self.assertTrue(entered.wait(2))
                with patch("caption.vocab_tab.vocab.build_prompt", return_value="Translate next word"):
                    self.assertTrue(panel._send_text("next word"))
                next_request = panel._active_request_id
                panel.set_busy(True)
                panel._on_partial("Next word streaming response")
            finally:
                release.set()
            self.finish_upload(panel)

        self.assertTrue(panel._busy)
        self.assertEqual(panel._active_request_id, next_request)
        self.assertTrue(panel._requests[next_request].active)
        self.assertTrue(any(m[0] == "stream" and "Next word streaming" in m[2] for m in panel._messages))
        self.assertIn("Saved to Voca", next(m for m in panel._messages if m[0] == "bot")[2])

    def test_progress_and_out_of_order_callbacks_update_only_the_matching_answer(self):
        panel = self.panel()
        panel._on_done(self.translation())
        first = next(m for m in panel._messages if m[0] == "bot")
        panel._on_done(VocabTranslation([vocab.NewWord("second", translation="second meaning")], "test-key", "different", "Other source"))
        second = [m for m in panel._messages if m[0] == "bot"][1]
        panel._update_voca_message([vocab.NewWord("example", voca_error="Saving to Voca…")], first)
        panel._on_voca_saved([vocab.NewWord("second", translation="second meaning", voca_status="created")], second)
        panel._on_voca_saved([vocab.NewWord("example", translation="first meaning", voca_status="created")], first)
        answers = [m for m in panel._messages if m[0] == "bot"]
        self.assertIn("first meaning", answers[0][2])
        self.assertIn("second meaning", answers[1][2])
        self.assertEqual([m[3] for m in answers], [first[3], second[3]])

    def test_late_upload_after_new_conversation_does_not_restore_old_cards(self):
        panel = self.panel()
        panel._on_done(self.translation())
        first = next(m for m in panel._messages if m[0] == "bot")
        panel.new_conversation()
        panel._on_partial("new conversation stream")
        panel._on_voca_saved([vocab.NewWord("example", voca_status="created")], first)
        self.assertFalse(any(m[0] == "bot" for m in panel._messages))
        self.assertEqual(panel._messages[0][0], "stream")

    def test_close_warns_for_deferred_upload_and_does_not_start_it_after_close(self):
        panel = self.panel()
        panel._on_done(self.translation())
        with patch("caption.vocab_tab.messagebox.askyesno", return_value=False) as confirm:
            self.assertFalse(panel.confirm_close())
        confirm.assert_called_once()
        self.assertEqual(panel._voca_pending_count(), 1)
        panel.close()
        panel.after_idle.call_args.args[0]()
        self.assertIsNone(panel._voca_queue)

    def test_full_queue_marks_only_its_answer_failed_without_locking_chat(self):
        panel = self.panel()
        panel._voca_queue = Mock(pending=0)
        panel._voca_queue.submit.return_value = False
        panel._on_done(self.translation())
        enqueue = panel.after_idle.call_args.args[0]
        panel._on_partial("Next response")
        enqueue()
        answer = next(m for m in panel._messages if m[0] == "bot")
        self.assertIn("Not saved", answer[2])
        self.assertIn("queue is full", answer[2])
        self.assertFalse(panel._busy)
        self.assertTrue(any(m[0] == "stream" for m in panel._messages))

    def test_declining_exit_keeps_app_and_recording_open(self):
        from caption.gui import App

        app = object.__new__(App)
        app.vocab = Mock()
        app.vocab.confirm_close.return_value = False
        app.save_settings, app.stop, app.root = Mock(), Mock(), Mock()
        app.on_close()
        app.save_settings.assert_not_called()
        app.stop.assert_not_called()
        app.vocab.close.assert_not_called()
        app.root.destroy.assert_not_called()

    def test_without_api_key_response_is_displayed_without_upload(self) -> None:
        panel = self.panel()
        panel._on_done(self.translation(api_key=""))
        response = next(m for m in panel._messages if m[0] == "bot")
        self.assertIn("an illustration", response[2])
        panel.after_idle.assert_not_called()
        self.assertFalse(panel._busy)


if __name__ == "__main__":
    unittest.main()
