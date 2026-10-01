from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from caption import chatgpt
from caption.chat_tab import ChatPanel, ChatRequest
from caption.gui import App
from caption.vocab_tab import VocabPanel, VocabRequest


class RetryTests(unittest.TestCase):
    def test_cancel_sets_the_active_request_event(self) -> None:
        app = object.__new__(App)
        app._sending_to_chatgpt = True
        app._chatgpt_cancel = threading.Event()
        app.status_var = SimpleNamespace(set=Mock())

        app.cancel_chatgpt()

        self.assertTrue(app._chatgpt_cancel.is_set())
        app.status_var.set.assert_called_once_with("ChatGPT: cancelling…")

    def test_chatgpt_cancel_clicks_stop_and_aborts_wait(self) -> None:
        page, stop = Mock(), Mock()
        page.locator.return_value.filter.return_value.first = stop
        stop.count.return_value = 1
        stop.is_visible.return_value = True
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(chatgpt.ChatGPTCancelled):
            chatgpt._cancel_if_requested(page, cancel_event)

        page.locator.assert_called_once_with(chatgpt.STOP_BUTTON)
        page.locator.return_value.filter.assert_called_once_with(visible=True)
        stop.click.assert_called_once_with(timeout=2000)

    def test_chat_retry_reuses_failed_job_and_conversation(self) -> None:
        panel = object.__new__(ChatPanel)
        job = Mock()
        panel._busy = False
        panel._requests = {
            "request-1": ChatRequest("question", None, "conversation-1", job=job, failed=True)
        }
        panel._active_request_id = None
        panel._active_job = None
        panel._pending_conversation_id = None
        panel._set_pending = Mock()
        panel._run_chatgpt = Mock(return_value=True)

        panel.retry("request-1")

        self.assertIs(panel._active_job, job)
        self.assertEqual(panel._active_request_id, "request-1")
        self.assertEqual(panel._pending_conversation_id, "conversation-1")
        panel._run_chatgpt.assert_called_once_with(job, panel._on_answer, panel._on_error)
        panel._set_pending.assert_called_once_with("Retrying with ChatGPT…")

    def test_summary_retry_reuses_failed_summary_job(self) -> None:
        app = object.__new__(App)
        job, transcript = Mock(), Mock()
        app._sending_to_chatgpt = False
        app._summary_retry = (job, transcript, True, True)
        app._summary_retry_failed = True
        app._summary_active = None
        app.summary_retry_btn = Mock()
        app.status_var = SimpleNamespace(set=Mock())
        app._run_chatgpt = Mock(return_value=True)

        app.retry_summary()

        self.assertEqual(app._summary_active, (job, transcript, True, True))
        self.assertIs(app._run_chatgpt.call_args.args[0], job)
        app.status_var.set.assert_called_once_with("ChatGPT: retrying summary…")

    def test_new_words_retry_reuses_failed_job(self) -> None:
        panel = object.__new__(VocabPanel)
        job = Mock()
        panel._busy = False
        panel._requests = {"request-1": VocabRequest("word", job=job, failed=True)}
        panel._active_request_id = None
        panel._active_job = None
        panel._set_pending = Mock()
        panel._run_chatgpt = Mock(return_value=True)

        panel.retry("request-1")

        self.assertIs(panel._active_job, job)
        self.assertEqual(panel._active_request_id, "request-1")
        panel._run_chatgpt.assert_called_once_with(job, panel._on_done, panel._on_error)
        panel._set_pending.assert_called_once_with("Retrying with ChatGPT…")


    def test_successful_chat_retry_resends_message_and_image_with_fresh_context(self) -> None:
        panel = object.__new__(ChatPanel)
        image = Mock()
        panel._busy = False
        panel._requests = {
            "request-1": ChatRequest("question", image, "conversation-1", failed=False)
        }
        panel._send_message = Mock(return_value=True)

        panel.retry("request-1")

        panel._send_message.assert_called_once_with("question", image, reuse_image=True)

    def test_successful_new_words_retry_creates_a_fresh_request(self) -> None:
        panel = object.__new__(VocabPanel)
        panel._busy = False
        panel._requests = {"request-1": VocabRequest("alpha\nbeta", failed=False)}
        panel._send_text = Mock(return_value=True)

        panel.retry("request-1")

        panel._send_text.assert_called_once_with("alpha\nbeta")

    def test_successful_summary_retry_builds_a_fresh_request(self) -> None:
        app = object.__new__(App)
        app._sending_to_chatgpt = False
        app._summary_retry = (Mock(), Mock(), True, True)
        app._summary_retry_failed = False
        app.summarize_with_chatgpt = Mock()

        app.retry_summary()

        app.summarize_with_chatgpt.assert_called_once_with()

    def test_chat_message_actions_follow_the_individual_request(self) -> None:
        panel = object.__new__(ChatPanel)
        panel._requests = {
            "request-1": ChatRequest("question", None, "conversation-1", active=True)
        }
        panel._busy = True

        active_html = panel._request_actions("request-1")
        self.assertIn("rtc-chat-stop:request-1", active_html)
        self.assertNotIn("rtc-chat-retry:request-1", active_html)

        panel._busy = False
        retry_html = panel._request_actions("request-1")
        self.assertIn("rtc-chat-retry:request-1", retry_html)

    def test_vocab_message_actions_follow_the_individual_request(self) -> None:
        panel = object.__new__(VocabPanel)
        panel._requests = {"request-1": VocabRequest("word", active=True)}
        panel._busy = True

        active_html = panel._request_actions("request-1")
        self.assertIn("rtc-vocab-stop:request-1", active_html)
        self.assertNotIn("rtc-vocab-retry:request-1", active_html)

        panel._busy = False
        retry_html = panel._request_actions("request-1")
        self.assertIn("rtc-vocab-retry:request-1", retry_html)

    def test_chat_html_action_is_deferred_until_tk_is_idle(self) -> None:
        panel = object.__new__(ChatPanel)
        panel.after_idle = Mock()
        panel.retry = Mock()

        panel._on_history_link("rtc-chat-retry:request-1")

        panel.retry.assert_not_called()
        callback = panel.after_idle.call_args.args[0]
        callback()
        panel.retry.assert_called_once_with("request-1")

    def test_vocab_html_action_is_deferred_until_tk_is_idle(self) -> None:
        panel = object.__new__(VocabPanel)
        panel.after_idle = Mock()
        panel.retry = Mock()

        panel._on_history_link("rtc-vocab-retry:request-1")

        panel.retry.assert_not_called()
        callback = panel.after_idle.call_args.args[0]
        callback()
        panel.retry.assert_called_once_with("request-1")

    def test_busy_renders_are_coalesced(self) -> None:
        panel = object.__new__(ChatPanel)
        panel._render_scheduled = False
        panel.after_idle = Mock()
        panel.winfo_exists = Mock(return_value=True)
        panel._render = Mock()

        panel._schedule_render()
        panel._schedule_render()

        panel.after_idle.assert_called_once()
        panel.after_idle.call_args.args[0]()
        panel._render.assert_called_once_with()
        self.assertFalse(panel._render_scheduled)


if __name__ == "__main__":
    unittest.main()
