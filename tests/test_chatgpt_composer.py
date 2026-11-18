"""Exercise composer fallback in isolated Chrome, without contacting ChatGPT."""
import threading
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from caption import chatgpt


class ComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content('''<form onsubmit="event.preventDefault();window.sent++">
            <div id="editor" contenteditable="true" role="textbox">Old draft</div>
            <button>Send</button></form><script>
            window.sent=0; window.inputs=0;
            editor.addEventListener('input', () => window.inputs++);
            </script>''')
        self.editor = self.page.locator('#editor')
        self.prompt = 'Explain this:\nTiếng Việt 日本語 <tag> & text\nAnother paragraph.'

    def test_normal_fill_matches_prompt_without_submitting(self):
        chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.editor.inner_text(), self.prompt)
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_timeout_fallback_fires_editor_input_and_preserves_other_tab(self):
        other = self.browser.new_page()
        self.addCleanup(other.close)
        other.set_content('<textarea>Keep my draft</textarea>')
        other.bring_to_front()
        with patch.object(self.editor, 'fill', side_effect=PlaywrightTimeout('PRIVATE TRANSCRIPT')):
            chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.editor.inner_text(), self.prompt)
        self.assertGreater(self.page.evaluate('window.inputs'), 0)
        self.assertEqual(other.locator('textarea').input_value(), 'Keep my draft')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_fill_timeout_after_insertion_does_not_insert_twice(self):
        original = self.editor.fill
        input_counts = []
        def fill_then_fail(text, **kwargs):
            original(text, **kwargs)
            input_counts.append(self.page.evaluate('window.inputs'))
            raise PlaywrightTimeout('timed out after input')
        with patch.object(self.editor, 'fill', side_effect=fill_then_fail):
            chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.editor.inner_text(), self.prompt)
        self.assertEqual(len(input_counts), 1)
        self.assertEqual(self.page.evaluate('window.inputs'), input_counts[0])

    def test_paused_animation_frames_can_use_fallback(self):
        # Simulate a background page whose animation callbacks stop advancing.
        self.page.evaluate('window.requestAnimationFrame = () => 1')
        chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.editor.inner_text(), self.prompt)
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_disabled_or_readonly_input_is_not_forced_and_error_is_private(self):
        for attribute in ('disabled', 'readonly'):
            self.page.set_content(f'<textarea id="editor" {attribute}>Keep</textarea>')
            with self.subTest(attribute=attribute), \
                    patch.object(self.editor, 'fill', side_effect=PlaywrightTimeout('PRIVATE TRANSCRIPT')):
                with self.assertRaises(chatgpt.ChatGPTError) as raised:
                    chatgpt._fill_composer(self.page, self.editor, self.prompt)
                self.assertNotIn('PRIVATE TRANSCRIPT', str(raised.exception))
                self.assertEqual(self.editor.input_value(), 'Keep')

    def test_cancel_after_failed_fill_does_not_try_fallback(self):
        cancel = threading.Event()
        def fail(*args, **kwargs):
            cancel.set()
            raise PlaywrightTimeout('Cancelled')
        with patch.object(self.editor, 'fill', side_effect=fail):
            with self.assertRaises(chatgpt.ChatGPTCancelled):
                chatgpt._fill_composer(self.page, self.editor, self.prompt, cancel)
        self.assertEqual(self.editor.inner_text(), 'Old draft')

    def test_editor_that_rejects_changes_cannot_send_wrong_text(self):
        self.page.evaluate("editor.addEventListener('input', () => editor.textContent = 'Rejected')")
        with patch.object(self.editor, 'fill', side_effect=PlaywrightTimeout('Timeout')):
            with self.assertRaises(chatgpt.ChatGPTError):
                chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_textarea_fallback_updates_input_value(self):
        self.page.set_content('<textarea id="editor">Old draft</textarea>')
        with patch.object(self.editor, 'fill', side_effect=PlaywrightTimeout('Timeout')):
            chatgpt._fill_composer(self.page, self.editor, self.prompt)
        self.assertEqual(self.editor.input_value(), self.prompt)
