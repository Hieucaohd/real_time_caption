from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from caption import chatgpt, prompt_versions
from caption.chat_tab import ChatPanel


class ChatImageTests(unittest.TestCase):
    def panel(self, folder: Path) -> ChatPanel:
        panel = object.__new__(ChatPanel)
        panel._busy = False
        panel._selected_image = Image.new("RGBA", (48, 32), (255, 0, 0, 255))
        panel._get_conversation_id = Mock(return_value="conversation")
        panel._store = Mock()
        panel._store.get.return_value = SimpleNamespace(screenshots_dir=folder / "screenshots")
        panel._selected_chrome_tab_key = Mock(return_value="chrome-tab")
        panel.prompt_var = Mock()
        panel.prompt_var.get.return_value = "prompt"
        panel.settings = SimpleNamespace(chat_prompt="")
        panel.attach_var = Mock()
        panel.attach_var.get.return_value = False
        panel.history_var = Mock()
        panel.history_var.get.return_value = False
        panel.screenshot_var = Mock()
        panel.screenshot_var.get.return_value = True
        panel._capture_screen = Mock()
        panel._new_conversation = False
        panel._power = Mock(return_value=None)
        panel._tab_results = queue.Queue()
        panel._run_chatgpt = Mock(return_value=True)
        panel._add_message = Mock(return_value="chat-1")
        panel._set_pending = Mock()
        panel._requests = {}
        return panel

    def test_upload_is_saved_and_sent_instead_of_auto_screenshot(self):
        with tempfile.TemporaryDirectory() as folder:
            panel = self.panel(Path(folder))
            with (
                patch.object(prompt_versions, "resolve", return_value="prompt"),
                patch.object(prompt_versions, "path_for", return_value=Path("prompt.txt")),
                patch.object(chatgpt, "build_prompt", return_value="Explain this image"),
                patch.object(chatgpt, "ask", return_value="Answer") as ask,
            ):
                self.assertTrue(panel._send_message("Explain this image"))
                image_path = panel._requests["chat-1"].image
                self.assertEqual(image_path.parent, Path(folder) / "screenshots")
                with Image.open(image_path) as saved:
                    self.assertEqual(saved.getpixel((0, 0)), (255, 0, 0, 255))
                panel._capture_screen.assert_not_called()
                self.assertEqual(panel._add_message.call_args.kwargs["image"], image_path)
                job = panel._run_chatgpt.call_args.args[0]
                job(Mock(), threading.Event())
                self.assertEqual(ask.call_args.args[1], [image_path])

    def test_retry_reuses_original_image_even_with_a_different_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            panel = self.panel(Path(folder))
            original = panel._save_selected_image("conversation")
            panel._selected_image = Image.new("RGB", (12, 12), "blue")
            with (
                patch.object(prompt_versions, "resolve", return_value="prompt"),
                patch.object(prompt_versions, "path_for", return_value=Path("prompt.txt")),
                patch.object(chatgpt, "build_prompt", return_value="Explain this image"),
                patch.object(panel, "_save_selected_image") as save,
            ):
                self.assertTrue(panel._send_message("Explain this image", original, reuse_image=True))
            save.assert_not_called()
            panel._capture_screen.assert_not_called()
            self.assertEqual(panel._requests["chat-1"].image, original)

    def test_selected_file_is_snapshotted_and_can_be_deleted_before_send(self):
        with tempfile.TemporaryDirectory() as folder:
            panel = self.panel(Path(folder))
            panel.image_label, panel.remove_image_btn = Mock(), Mock()
            source = Path(folder) / "photo.png"
            Image.new("RGB", (10, 10), "red").save(source)
            with patch("caption.chat_tab.ImageTk.PhotoImage", return_value=Mock()):
                with Image.open(source) as image:
                    panel._select_image(image, source.name)
            source.unlink()
            saved = panel._save_selected_image("conversation")
            with Image.open(saved) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

    def test_control_v_preserves_normal_text_paste(self):
        panel = object.__new__(ChatPanel)
        panel._busy = False
        with patch("caption.chat_tab.ImageGrab.grabclipboard", return_value=None):
            self.assertIsNone(panel._on_paste())
        panel._select_image = Mock()
        image = Image.new("RGB", (8, 8))
        with patch("caption.chat_tab.ImageGrab.grabclipboard", return_value=image):
            self.assertEqual(panel._on_paste(), "break")
        panel._select_image.assert_called_once_with(image, "Clipboard image")

    def test_send_clears_draft_only_when_request_is_accepted(self):
        panel = object.__new__(ChatPanel)
        panel._busy = False
        panel.entry = Mock()
        panel.entry.get.return_value = "message"
        panel._send_message = Mock(return_value=False)
        panel._clear_image = Mock()
        panel.send()
        panel.entry.delete.assert_not_called()
        panel._clear_image.assert_not_called()
        panel._send_message.return_value = True
        panel.send()
        panel.entry.delete.assert_called_once_with("1.0", "end")
        panel._clear_image.assert_called_once_with()

    def test_image_can_be_sent_without_typing_a_message(self):
        panel = object.__new__(ChatPanel)
        panel._busy = False
        panel._selected_image = Image.new("RGB", (8, 8))
        panel.entry = Mock()
        panel.entry.get.return_value = ""
        panel._send_message = Mock(return_value=True)
        panel._clear_image = Mock()
        panel.send()
        panel._send_message.assert_called_once_with("Hãy giải thích nội dung ảnh này.")
        panel._clear_image.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
