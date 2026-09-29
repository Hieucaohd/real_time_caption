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
            app.prompt_var = SimpleNamespace(get=lambda: "v1 - default")
            app.new_chat_var = SimpleNamespace(get=lambda: True)
            app.chat = SimpleNamespace(wants_new_conversation=False)
            app._summary_power = Mock(return_value=None)
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


if __name__ == "__main__":
    unittest.main()
