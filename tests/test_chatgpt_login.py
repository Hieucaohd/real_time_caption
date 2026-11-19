import unittest

from caption import chatgpt


class ChatGPTLoginTests(unittest.TestCase):
    def test_detects_sidebar_login_controls_and_ignores_conversation_content(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page()
                page.route('https://chatgpt.com/**', lambda r: r.fulfill(content_type='text/html', body='<main></main>'))
                page.goto('https://chatgpt.com/')
                profile = '<nav><button aria-haspopup="menu" aria-label="Open profile menu" hidden>Account</button></nav>'
                for html, expected in (
                    (profile, 'signed_in'),
                    ('<header><button data-testid="login-button">Log in</button></header>', 'signed_out'),
                    ('<header><a href="/login">Đăng nhập</a></header>', 'signed_out'),
                    ('<main>Loading…</main>', 'unknown'),
                    ('<div data-message-author-role="assistant"><button>Log in</button>' + profile + '</div>', 'unknown'),
                    (profile + '<header><button>Log in</button></header>', 'signed_out'),
                ):
                    with self.subTest(expected=expected, html=html):
                        page.set_content(html)
                        self.assertEqual(chatgpt.login_status(page), expected)
                page.set_content('<main>Loading…</main>')
                with self.assertRaises(chatgpt.ChatGPTLoginUnverified):
                    chatgpt.require_page_login(page, timeout=0)
                page.set_content('<main><button>Verify you are human</button></main>')
                with self.assertRaises(chatgpt.ChatGPTVerificationRequired):
                    chatgpt.require_page_login(page, timeout=0)
            finally:
                browser.close()
