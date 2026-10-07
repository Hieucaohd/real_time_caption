from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption import chatgpt, markdown_html
from caption.chat_tab import ChatPanel
from caption.gui import App
from caption.stream_preview import StreamPreview
from caption.vocab_tab import VocabPanel, VocabTranslation
from caption.vocab import NewWord


class StreamingTests(unittest.TestCase):
    def test_wait_streams_changed_text_and_locks_to_the_answer_turn(self):
        page = Mock()
        page.evaluate.side_effect = [
            {"id": "user", "text": "", "complete": False},
            {"id": "answer", "text": "Hello", "complete": False},
            {"id": "answer", "text": "Hello", "complete": False},
            {"id": "answer", "text": "Hello world", "complete": True},
        ]
        partial = Mock()
        with patch("caption.chatgpt.time.sleep"):
            result = chatgpt._wait_new_answer(
                page, ["old-answer"], 5, on_partial=partial, turns_before=["old-turn"]
            )
        self.assertEqual(result, "answer")
        self.assertEqual([c.args[0] for c in partial.call_args_list], ["Hello", "Hello world"])
        self.assertIsNone(page.evaluate.call_args_list[1].args[1]["target"])
        self.assertEqual(page.evaluate.call_args_list[2].args[1]["target"], "answer")
        self.assertEqual(page.evaluate.call_args.args[1]["before"], ["old-turn"])

    def test_cancel_aborts_before_another_preview_is_read(self):
        event = threading.Event()
        event.set()
        page, partial = Mock(), Mock()
        with self.assertRaises(chatgpt.ChatGPTCancelled):
            chatgpt._wait_new_answer(page, [], 5, event, partial)
        page.evaluate.assert_not_called()
        partial.assert_not_called()

    def test_ui_queue_coalesces_updates_and_only_renders_on_ui_drain(self):
        callbacks, display = queue.Queue(), Mock()
        preview = StreamPreview(callbacks, display)
        worker = threading.Thread(target=lambda: [preview.push(str(i)) for i in range(100)])
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        display.assert_not_called()
        self.assertEqual(callbacks.qsize(), 1)
        callbacks.get_nowait()()
        display.assert_called_once_with("99")
        preview.push("100")
        callbacks.get_nowait()()
        self.assertEqual(display.call_args.args[0], "100")

    def test_delayed_preview_cannot_overwrite_final_response(self):
        callbacks, display = queue.Queue(), Mock()
        preview = StreamPreview(callbacks, display)
        preview.push("unfinished")
        preview.close()
        preview.push("too late")
        callbacks.get_nowait()()
        display.assert_not_called()
        self.assertTrue(callbacks.empty())

    def test_chat_preview_is_replaced_and_not_saved_to_history(self):
        panel = object.__new__(ChatPanel)
        panel._pending_conversation_id = "conversation"
        panel._get_conversation_id = Mock(return_value="conversation")
        panel._store = Mock()
        panel._schedule_render = Mock()
        panel._render = Mock()
        panel._messages = [("user", "You", "question", None)]
        panel._on_partial("partial <code>")
        panel._on_partial("complete preview")
        self.assertEqual(len(panel._messages), 2)
        self.assertEqual(panel._messages[-1][0], "stream")
        panel._store.add_message.assert_not_called()
        panel._set_pending(None)
        self.assertEqual(len(panel._messages), 1)

    def test_chat_preview_does_not_leak_into_another_conversation(self):
        panel = object.__new__(ChatPanel)
        panel._pending_conversation_id = "first"
        panel._get_conversation_id = Mock(return_value="second")
        panel._messages = []
        panel._schedule_render = Mock()
        panel._on_partial("first conversation's response")
        self.assertEqual(panel._messages, [])
        panel._schedule_render.assert_not_called()

    def test_summary_preview_is_transient_and_escapes_html(self):
        app = object.__new__(App)
        app._conversation_id = Mock(return_value="conversation")
        app.summary, app.store = Mock(), Mock()
        with patch.object(markdown_html, "show") as show:
            app._on_summary_partial(SimpleNamespace(conversation_id="conversation"), "<script>partial")
        self.assertIn("&lt;script&gt;partial", show.call_args.args[1])
        self.assertTrue(show.call_args.kwargs["scroll_to_end"])
        app.store.add_message.assert_not_called()

    def test_vocab_final_cards_replace_stream_before_upload(self):
        panel = object.__new__(VocabPanel)
        panel._messages = [("user", "You", "example", None)]
        panel._requests = {}
        panel._active_request_id = None
        panel._schedule_render = Mock()
        panel._render = Mock()
        panel._on_partial('[{"word":"exam')
        panel._on_done(VocabTranslation([NewWord("example", translation="illustration")], "", "", ""))
        self.assertFalse(any(m[0] == "stream" for m in panel._messages))
        self.assertEqual(len(panel._messages), 2)
        self.assertIn("illustration", panel._messages[-1][2])


if __name__ == "__main__":
    unittest.main()
