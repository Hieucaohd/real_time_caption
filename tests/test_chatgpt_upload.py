"""Slow upload readiness with a virtual clock; never send to real ChatGPT."""
import threading
import unittest
from unittest.mock import Mock, patch

from caption import chatgpt


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.clock = patch.object(chatgpt.time, 'monotonic', side_effect=lambda: self.now)
        self.sleep = patch.object(chatgpt.time, 'sleep', side_effect=self.advance)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)

    def advance(self, seconds):
        self.now += seconds

    def test_upload_ready_after_three_minutes_is_allowed_with_new_budget(self):
        button, status = Mock(), Mock()
        button.is_enabled.side_effect = lambda: self.now >= 180
        progress = chatgpt._upload_progress(status, self.now, 600)
        with patch.object(chatgpt, '_visible', return_value=button):
            self.assertIs(chatgpt._wait_send_enabled(Mock(), 600, on_wait=progress), button)
        self.assertEqual(self.now, 180)
        button.click.assert_not_called()
        self.assertGreater(status.call_count, 30)
        self.assertLessEqual(status.call_count, 37)
        self.assertIn('180/600', status.call_args.args[0])

    def test_timeout_stays_bounded_and_does_not_send(self):
        button = Mock()
        button.is_enabled.return_value = False
        with patch.object(chatgpt, '_visible', return_value=button):
            with self.assertRaises(chatgpt.ChatGPTError):
                chatgpt._wait_send_enabled(Mock(), 600)
        self.assertEqual(self.now, 600)
        button.click.assert_not_called()

    def test_stop_interrupts_upload_wait_without_waiting_ten_minutes(self):
        cancel, button = threading.Event(), Mock()
        button.is_enabled.return_value = False
        def waiting():
            if self.now >= 3:
                cancel.set()
        with patch.object(chatgpt, '_visible', return_value=button):
            with self.assertRaises(chatgpt.ChatGPTCancelled):
                chatgpt._wait_send_enabled(Mock(), 600, cancel, on_wait=waiting)
        self.assertLessEqual(self.now, 3.5)
        button.click.assert_not_called()

    def test_stop_interrupts_wait_for_attachment_chips(self):
        from pathlib import Path
        cancel, form = threading.Event(), Mock()
        form.locator.return_value.evaluate_all.return_value = []
        def waiting():
            cancel.set()
        with self.assertRaises(chatgpt.ChatGPTCancelled):
            chatgpt._wait_attachments_visible(form, [Path('image.png')], 600, cancel, on_wait=waiting)
        self.assertLessEqual(self.now, .25)
