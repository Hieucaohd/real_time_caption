from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption import chatgpt, prompt_versions
from caption.gui import App


class SummaryAttachmentTests(unittest.TestCase):
    def test_summary_tab_dropdown_restores_its_saved_chrome_tab(self) -> None:
        app = object.__new__(App)
        app._summary_scanning_tabs = True
        app._sending_to_chatgpt = False
        app.settings = SimpleNamespace(summary_chatgpt_selected_tab="tab-2")
        app.summary_chrome_tab_box = Mock()
        app.summary_refresh_tabs_btn = Mock()
        app.summary_chrome_tab_status = Mock()
        selected = {"value": ""}
        app.summary_chrome_tab_var = Mock()
        app.summary_chrome_tab_var.set.side_effect = lambda value: selected.update(value=value)
        app.summary_chrome_tab_var.get.side_effect = lambda: selected["value"]
        tabs = [
            chatgpt.ChromeChatGPTTab("tab-1", "First — 11111111", "First", "url-1", True),
            chatgpt.ChromeChatGPTTab("tab-2", "Second — 22222222", "Second", "url-2", False),
        ]

        app._show_summary_chrome_tabs(tabs)

        self.assertEqual(selected["value"], "Second — 22222222")
        self.assertEqual(app.settings.summary_chatgpt_selected_tab, "tab-2")
        app.summary_chrome_tab_box.configure.assert_any_call(
            values=["First — 11111111", "Second — 22222222"]
        )

    def test_summary_requires_a_selected_chrome_tab(self) -> None:
        app = object.__new__(App)
        app.session_file = Mock()
        app._sending_to_chatgpt = False
        app._selected_summary_chrome_tab_key = Mock(return_value=None)

        with patch("caption.gui.messagebox.showinfo") as showinfo:
            app.summarize_with_chatgpt()

        showinfo.assert_called_once()

    def test_summary_sends_only_the_transcript_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            upload = Path(folder) / "transcript.txt"
            upload.write_text("caption", encoding="utf-8")
            transcript = SimpleNamespace(
                has_text=True,
                snapshot=Mock(return_value=upload),
                source="Windows Live Captions",
                started=datetime(2026, 9, 29, 9, 0),
                conversation_id="conversation-1",
            )
            app = object.__new__(App)
            app.session_file = transcript
            app._sending_to_chatgpt = False
            app._summary_max_lines = Mock(return_value=300)
            app.summary_attach_transcript_var = SimpleNamespace(get=lambda: True)
            app.prompt_var = SimpleNamespace(get=lambda: "v1 - default")
            app.new_chat_var = SimpleNamespace(get=lambda: True)
            app.chat = SimpleNamespace(wants_new_conversation=False)
            app._summary_power = Mock(return_value=None)
            app._selected_summary_chrome_tab_key = Mock(return_value="summary-tab")
            app._summary_received = Mock()

            def run(job, _on_answer, _on_error):
                job(lambda _status: None, threading.Event())
                return True

            app._run_chatgpt = run

            with (
                patch.object(prompt_versions, "resolve", return_value="v1 - default"),
                patch.object(prompt_versions, "path_for", return_value=Path(folder) / "prompt.txt"),
                patch.object(chatgpt, "build_prompt", return_value="Summarize") as build_prompt,
                patch.object(chatgpt, "ask", return_value="Summary") as ask,
            ):
                app.summarize_with_chatgpt()

            build_prompt.assert_called_once()
            self.assertEqual(ask.call_args.args[1], upload)
            self.assertIsNotNone(app._summary_retry)
            self.assertFalse(ask.call_args.kwargs["cancel_event"].is_set())
            self.assertEqual(ask.call_args.kwargs["selected_tab"], "summary-tab")
            self.assertNotIn("use_current_tab", ask.call_args.kwargs)

    def test_summary_can_skip_the_transcript_attachment(self) -> None:
        transcript = SimpleNamespace(
            has_text=False,
            snapshot=Mock(),
            source="Windows Live Captions",
            started=datetime(2026, 9, 30, 9, 0),
            conversation_id="conversation-1",
        )
        app = object.__new__(App)
        app.session_file = transcript
        app._sending_to_chatgpt = False
        app._summary_max_lines = Mock(return_value=300)
        app.summary_attach_transcript_var = SimpleNamespace(get=lambda: False)
        app.prompt_var = SimpleNamespace(get=lambda: "v1 - default")
        app.new_chat_var = SimpleNamespace(get=lambda: False)
        app.chat = SimpleNamespace(wants_new_conversation=False)
        app._summary_power = Mock(return_value=None)
        app._selected_summary_chrome_tab_key = Mock(return_value="summary-tab")

        def run(job, _on_answer, _on_error):
            job(lambda _status: None, threading.Event())
            return True

        app._run_chatgpt = run

        with (
            patch.object(prompt_versions, "resolve", return_value="v1 - default"),
            patch.object(prompt_versions, "path_for", return_value=Path("prompt.txt")),
            patch.object(chatgpt, "build_prompt", return_value="Summarize") as build_prompt,
            patch.object(chatgpt, "ask", return_value="Summary") as ask,
        ):
            app.summarize_with_chatgpt()

        transcript.snapshot.assert_not_called()
        app._summary_max_lines.assert_not_called()
        self.assertIsNone(build_prompt.call_args.args[0])
        self.assertIsNone(ask.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
