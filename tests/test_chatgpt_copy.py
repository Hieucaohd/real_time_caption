from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from caption import chatgpt, markdown_html


class ReadAnswerTests(unittest.TestCase):
    def test_waits_for_delayed_copy_and_preserves_markdown(self):
        page = Mock()
        markdown = "## Answer\n\n**Important**\n\n" + r"\[x_1 = 2\]"
        reads = iter([[], [], [{"k": "text/plain", "t": markdown}]])
        page.title.return_value = 'Title at response completion'
        on_tab = Mock()
        on_source = Mock()
        page.locator.return_value.first.evaluate.return_value = {
            'url': 'https://chatgpt.com/c/conversation-one?tracking=x#old', 'message_id': 'response-one',
        }
        page.evaluate.side_effect = lambda script: (
            next(reads) if script == chatgpt.COPY_READ else
            True if script == chatgpt.COPY_HOOK else []
        )
        with patch.object(chatgpt.time, "sleep"):
            self.assertEqual(chatgpt._read_answer(page, "answer", on_tab, on_source), markdown)
        on_tab.assert_called_once_with('Title at response completion')
        on_source.assert_called_once_with({'response_conversation_url': 'https://chatgpt.com/c/conversation-one?tracking=x#old',
                                          'response_turn_id': 'answer', 'response_message_id': 'response-one', 'response_position': None})
        page.evaluate.assert_called_with(chatgpt.COPY_UNHOOK)

    def test_copy_failure_does_not_save_flattened_math(self):
        page = Mock()
        page.evaluate.side_effect = lambda script: True if script == chatgpt.COPY_HOOK else []
        with (
            patch.object(chatgpt.time, "monotonic", side_effect=[0, 4]),
            self.assertRaisesRegex(chatgpt.ChatGPTError, "Markdown could not be copied"),
        ):
            chatgpt._read_answer(page, "answer")
        page.evaluate.assert_called_with(chatgpt.COPY_UNHOOK)
        # The old fallback read innerText and duplicated MathML/KaTeX glyphs.
        page.locator.return_value.evaluate.assert_not_called()

    def test_tab_title_failure_does_not_discard_a_copied_answer(self):
        page = Mock()
        page.evaluate.side_effect = lambda script: True if script == chatgpt.COPY_HOOK else [{'k': 'text/plain', 't': 'Answer'}]
        page.title.side_effect = RuntimeError('Tab closed')
        on_tab = Mock()
        with self.assertLogs('caption.chatgpt', level='WARNING'):
            self.assertEqual(chatgpt._read_answer(page, 'answer', on_tab), 'Answer')
        on_tab.assert_called_once_with('')

    def test_click_failure_restores_hooks(self):
        page = Mock()
        page.evaluate.side_effect = lambda script: True if script == chatgpt.COPY_HOOK else []
        with patch.object(chatgpt, "_turn_locator") as locate:
            locate.return_value.locator.return_value.first.click.side_effect = RuntimeError("button gone")
            with self.assertRaises(chatgpt.ChatGPTError):
                chatgpt._read_answer(page, "answer")
        page.evaluate.assert_called_with(chatgpt.COPY_UNHOOK)

    def test_does_not_click_or_remove_hooks_owned_by_another_reader(self):
        page = Mock()
        page.evaluate.return_value = None
        with patch.object(chatgpt, "_turn_locator") as locate:
            with self.assertRaises(chatgpt.ChatGPTError):
                chatgpt._read_answer(page, "answer")
            locate.return_value.locator.return_value.first.click.assert_not_called()
        page.evaluate.assert_called_once_with(chatgpt.COPY_HOOK)


class ClipboardBrowserTests(unittest.TestCase):
    """Exercise real browser event propagation without touching the user's Chrome."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright, Error

        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)
        except Error as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chrome is needed for clipboard integration tests: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.route("https://copy.test/", lambda route: route.fulfill(
            content_type="text/html",
            body='<div data-turn-key="answer"><div class="turn-action-controls">'
                 '<button aria-label="Copy">Copy</button></div></div>',
        ))
        self.page.goto("https://copy.test/")
        self.page.evaluate("""() => {
          window.nativeWrites = 0;
          window.originalWrite = navigator.clipboard.write = async () => { nativeWrites++; };
          window.originalWriteText = navigator.clipboard.writeText = async () => { nativeWrites++; };
          window.originalExec = document.execCommand = () => { nativeWrites++; return true; };
        }""")

    def tearDown(self):
        self.context.close()

    def assert_restored(self):
        self.assertTrue(self.page.evaluate("""() =>
          !window.__rtcClipboard && nativeWrites === 0 &&
          document.execCommand === originalExec &&
          navigator.clipboard.write === originalWrite &&
          navigator.clipboard.writeText === originalWriteText
        """))

    def test_window_copy_handler_that_stops_propagation_preserves_formatting(self):
        markdown = "## Answer\n\n**Important**\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n" + r"\\[x\_1 = 2\\]"
        self.page.evaluate("""markdown => {
          document.querySelector('button').onclick = () => {
            window.addEventListener('copy', event => {
              event.preventDefault();
              event.stopImmediatePropagation();
              event.clipboardData.setData('text/plain', markdown);
              event.clipboardData.setData('text/html', '<h2>Answer</h2>');
            }, {capture:true, once:true});
            document.execCommand('copy');
          };
        }""", markdown)
        answer = chatgpt._read_answer(self.page, "answer")
        self.assertEqual(answer, markdown)
        with patch.object(markdown_html.mathjax_render, "display_html", return_value="<img>") as formula:
            rendered = markdown_html.to_html(answer)
        formula.assert_called_once_with("x_1 = 2")
        self.assertIn("<h2>Answer</h2>", rendered)
        self.assertIn("<strong>Important</strong>", rendered)
        self.assertIn("<table>", rendered)
        self.assert_restored()

    def test_async_rich_clipboard_copy_waits_for_blob(self):
        self.page.evaluate("""() => {
          document.querySelector('button').onclick = () => navigator.clipboard.write([
            new ClipboardItem({'text/plain': new Promise(resolve => setTimeout(() =>
              resolve(new Blob(['## Delayed answer'], {type:'text/plain'})), 800))})
          ]);
        }""")
        self.assertEqual(chatgpt._read_answer(self.page, "answer"), "## Delayed answer")
        self.assert_restored()

    def test_plain_clipboard_api_still_works(self):
        self.page.evaluate("""() => {
          document.querySelector('button').onclick = () => navigator.clipboard.writeText('**Answer**');
        }""")
        self.assertEqual(chatgpt._read_answer(self.page, "answer"), "**Answer**")
        self.assert_restored()

    def test_source_uses_exact_response_in_virtualized_project_conversation(self):
        from test_chatgpt_source import conversation_mapping
        url = 'https://chatgpt.com/g/project/c/conversation-one'
        # Full history contains 4 answers, but the DOM only mounts two turns.
        mapping = conversation_mapping()
        mapping['a4']['message']['id'] = 'chosen-response'
        self.page.route('https://chatgpt.com/backend-api/conversation/*', lambda route: route.fulfill(json={'mapping': mapping}))
        self.page.route(url, lambda route: route.fulfill(content_type='text/html', body='''
            <div data-turn-key="unrelated"><div data-message-author-role="assistant" data-message-id="wrong-response">Other</div></div>
            <div data-turn-key="chosen-turn">
              <div data-message-author-role="user" data-message-id="user-message">Question</div>
              <div data-content-search-unit-key="fallback-turn-98:2:assistant"
                   data-chatgpt-search-message-ids="chosen-response chosen-response">Answer</div>
              <button data-testid="copy-turn-action-button" onclick="navigator.clipboard.writeText('Chosen answer')">Copy</button>
            </div>'''))
        self.page.goto(url)
        source = {}
        answer = chatgpt._read_answer(self.page, 'chosen-turn', on_response_source=source.update)
        self.assertEqual(answer, 'Chosen answer')
        self.assertEqual(source, dict(response_conversation_url=url, response_turn_id='chosen-turn', response_message_id='chosen-response', response_position=4))
        # The source is a frozen snapshot, not a reference to the tab's future URL.
        self.page.evaluate("history.replaceState({}, '', '/c/a-different-conversation')")
        self.assertEqual(source['response_conversation_url'], url)
        self.page.locator('[data-content-search-unit-key]').evaluate("el=>{el.removeAttribute('data-chatgpt-search-message-ids');el.setAttribute('data-message-author-role','assistant');el.setAttribute('data-message-id','legacy-response')}")
        legacy = chatgpt._response_source(self.page.locator('[data-turn-key="chosen-turn"]'), 'chosen-turn')
        self.assertEqual(legacy['response_message_id'], 'legacy-response')
        self.assertIsNone(legacy['response_position'])

    def test_source_auth_retry_keeps_credentials_in_page_and_failure_keeps_answer(self):
        from test_chatgpt_source import conversation_mapping
        url = 'https://chatgpt.com/c/test?keep=query#keep-fragment'
        self.page.route('https://chatgpt.com/c/test?keep=query', lambda route: route.fulfill(content_type='text/html', body='''
            <div data-turn-key="answer"><div data-message-author-role="assistant" data-message-id="a4">Answer</div>
            <button data-testid="copy-turn-action-button" onclick="navigator.clipboard.writeText('Copied')">Copy</button></div>'''))
        self.page.route('https://chatgpt.com/api/auth/session', lambda route: route.fulfill(json={'accessToken': 'test-only-token'}))
        def conversation(route):
            if route.request.headers.get('authorization') == 'Bearer test-only-token':
                route.fulfill(json={'mapping': conversation_mapping()})
            else:
                route.fulfill(status=404, json={})
        self.page.route('https://chatgpt.com/backend-api/conversation/test', conversation)
        self.page.goto(url)
        source = {}
        self.assertEqual(chatgpt._read_answer(self.page, 'answer', on_response_source=source.update), 'Copied')
        self.assertEqual(source['response_position'], 4)
        self.assertEqual(source['response_conversation_url'], url)
        self.assertNotIn('test-only-token', str(source))
        self.page.route('https://chatgpt.com/backend-api/conversation/test', lambda route: route.fulfill(status=503, json={}))
        self.assertEqual(chatgpt._read_answer(self.page, 'answer', on_response_source=source.update), 'Copied')
        self.assertIsNone(source['response_position'])
        self.assertEqual(source['response_conversation_url'], url)

    def test_source_never_makes_a_conversation_url_from_an_unrelated_page(self):
        source = chatgpt._response_source(self.page.locator('[data-turn-key="answer"]'), 'answer')
        self.assertEqual(source['response_conversation_url'], '')
        self.assertEqual(source['response_turn_id'], 'answer')
        self.assertEqual(source['response_message_id'], '')


if __name__ == "__main__":
    unittest.main()
