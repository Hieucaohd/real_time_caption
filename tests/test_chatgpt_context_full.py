"""Detect the product notice without treating quoted conversation text as an error."""
import unittest

from playwright.sync_api import sync_playwright
from caption import chatgpt


class ContextFullTests(unittest.TestCase):
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

    def test_visible_notice_blocks_before_waiting_for_send(self):
        self.page.set_content('<h3>Context window full</h3><form><button disabled>Send</button></form>')
        with self.assertRaises(chatgpt.ChatGPTContextFull):
            chatgpt._wait_send_enabled(self.page.locator('form'), 30, page=self.page)

    def test_notice_while_waiting_for_answer_is_not_generic_timeout(self):
        self.page.set_content('<div role="alert"><strong>Context window full</strong></div>')
        with self.assertRaises(chatgpt.ChatGPTContextFull):
            chatgpt._wait_new_answer(self.page, [], 30)

    def test_hidden_notice_and_quoted_text_do_not_block(self):
        for container in (
            '<div hidden>{}</div>',
            '<div data-message-author-role="assistant">{}</div>',
            '<section data-testid="conversation-turn-1">{}</section>',
            '<div data-turn-key="answer-1">{}</div>',
            '<form data-chatgpt-composer><div contenteditable="true">{}</div></form>',
        ):
            with self.subTest(container=container):
                self.page.set_content(container.format('<span>Context window full</span>'))
                chatgpt._check_context_full(self.page)

    def test_quoted_text_before_real_notice_does_not_hide_notice(self):
        self.page.set_content('<div data-message-author-role="user">Context window full</div>'
                              '<h3>Context window full</h3>')
        with self.assertRaises(chatgpt.ChatGPTContextFull):
            chatgpt._check_context_full(self.page)
