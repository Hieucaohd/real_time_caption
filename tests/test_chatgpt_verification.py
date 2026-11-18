"""Read challenge notices in isolated pages without interacting with any challenge."""
import unittest
from playwright.sync_api import sync_playwright
from caption import chatgpt


class VerificationTests(unittest.TestCase):
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

    def test_visible_notice_raises_specific_actionable_error(self):
        self.page.set_content('<h2>Verify you are human</h2><button onclick="window.clicked=true">Verify</button>')
        with self.assertRaises(chatgpt.ChatGPTVerificationRequired) as caught:
            chatgpt._check_context_full(self.page)
        self.assertEqual(caught.exception.code, 'chatgpt_verification_required')
        self.assertIsNone(self.page.evaluate('window.clicked'))

    def test_challenge_form_is_detected_without_clicking(self):
        self.page.set_content('<form id="challenge-form">Checking your browser...</form>')
        with self.assertRaises(chatgpt.ChatGPTVerificationRequired):
            chatgpt._check_verification_required(self.page)

    def test_hidden_and_quoted_notices_are_ignored(self):
        for html in [
            '<h2 hidden>Verify you are human</h2>',
            '<div data-message-author-role="assistant"><h2>Verify you are human</h2></div>',
            '<section data-testid="conversation-turn-1">Verify you are human</section>',
            '<div contenteditable="true">Verify you are human</div>',
            '<form id="challenge-form" hidden>Checking your browser</form>',
            '<div data-turn-key="answer"><form id="challenge-form">Example</form></div>',
            '<title>Just a moment...</title><p>Normal page</p>',
        ]:
            with self.subTest(html=html):
                self.page.set_content(html)
                chatgpt._check_verification_required(self.page)

    def test_notice_interrupts_answer_wait(self):
        self.page.set_content('<h2>Verify you are human</h2>')
        with self.assertRaises(chatgpt.ChatGPTVerificationRequired):
            chatgpt._wait_new_answer(self.page, [], 600)

    def test_notice_interrupts_attachment_wait(self):
        from pathlib import Path
        self.page.set_content('<h2>Verify you are human</h2><form></form>')
        with self.assertRaises(chatgpt.ChatGPTVerificationRequired):
            chatgpt._wait_attachments_visible(self.page.locator('form'), [Path('image.png')], 600, page=self.page)
