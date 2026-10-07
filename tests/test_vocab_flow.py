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
        return panel

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
        self.assertIn("Saving to Voca", response[2])
        panel._render.assert_called()
        self.assertTrue(panel._busy)
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
                # A shared Chrome state update must not enable New words during saving.
                panel.set_busy(False)
                self.assertTrue(panel._busy)
            finally:
                release.set()
            panel._results.get(timeout=2)()

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
            patch("caption.vocab_tab.log.exception"),
        ):
            panel.after_idle.call_args.args[0]()
            panel._results.get(timeout=2)()

        responses = [m for m in panel._messages if m[0] == "bot"]
        self.assertEqual(len(responses), 1)
        self.assertIn("an illustration", responses[0][2])
        self.assertIn("Not saved", responses[0][2])
        self.assertFalse(panel._saving_to_voca)
        self.assertTrue(panel._busy)
        panel.set_busy(False)
        self.assertFalse(panel._busy)

    def test_without_api_key_response_is_displayed_without_upload(self) -> None:
        panel = self.panel()
        panel._on_done(self.translation(api_key=""))
        response = next(m for m in panel._messages if m[0] == "bot")
        self.assertIn("an illustration", response[2])
        panel.after_idle.assert_not_called()
        self.assertFalse(panel._busy)


if __name__ == "__main__":
    unittest.main()
