"""Dictation runs in an isolated browser with simulated controls, never a real mic."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption import chatgpt, chatgpt_dictation as dictation, paths
from caption.chatgpt_branch import _tab_snapshot


HTML = r'''<form onsubmit="event.preventDefault(); window.report('SEND')">
<div id="prompt-textarea" contenteditable="true"></div>
<div id="controls"><button type="button" aria-label="Dictate" onclick="begin()">Mic</button></div>
<button type="submit" data-testid="send-button" aria-label="Send prompt">Send</button>
<button type="button" aria-label="Start Voice" onclick="window.report('VOICE')">Voice</button>
</form><script>
function begin() {
  window.report('start');
  controls.innerHTML = '<button type="button" aria-label="Cancel" onclick="window.report(\'cancel\')">X</button><button type="button" aria-label="Submit dictation" onclick="finish()">Done</button>';
}
function finish() {
  window.report('finish'); controls.innerHTML = 'Transcribing...';
  setTimeout(() => {
    document.querySelector('#prompt-textarea').innerText = 'Transcript segment ' + window.segmentNumber;
    controls.innerHTML = '<button type="button" aria-label="Dictate" onclick="begin()">Mic</button>';
  }, 100);
}
</script>'''


class DictationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.context.route('https://chatgpt.com/**', lambda r: r.fulfill(body=HTML, content_type='text/html'))
        self.events, self.actions, self.created = [], [], []
        self.reader = dictation.ChatGPTDictationReader(self.events.append)
        self.reader.interval = .1
        self.counter = 0
        def new_page(_):
            page = self.context.new_page()
            self.created.append(page)
            self.assertLessEqual(sum(not p.is_closed() for p in self.created), 2)
            self.counter += 1
            number = self.counter
            page.add_init_script(f'window.segmentNumber = {number};')
            page.expose_function('report', lambda action: self.actions.append((number, action)))
            return page
        for context in (patch.object(paths, 'CACHE_DIR', Path(self.temp.name)),
                        patch.object(chatgpt, '_open_background_tab', side_effect=new_page)):
            context.start(); self.addCleanup(context.stop)

    def test_overlap_starts_next_before_stopping_old_and_pause_flushes_tail(self):
        def receive(event):
            self.events.append(event)
            if event.kind == 'final':
                self.reader.stop()
        self.reader._emit = receive
        try:
            self.reader._capture(self.browser)
        finally:
            self.reader._cleanup()
        self.assertEqual(self.actions, [(1, 'start'), (2, 'start'), (1, 'finish'), (2, 'finish')])
        self.assertEqual([e.text for e in self.events if e.kind == 'final'], ['Transcript segment 1', 'Transcript segment 2'])
        self.assertTrue(all(p.is_closed() for p in self.created))
        self.assertEqual(self.reader.segments, [])

    def test_sequential_mode_closes_each_tab_before_next_and_never_sends_prompt(self):
        self.reader.overlap = False
        def receive(event):
            self.events.append(event)
            if len([e for e in self.events if e.kind == 'final']) == 2:
                self.reader.stop()
        self.reader._emit = receive
        try:
            self.reader._capture(self.browser)
        finally:
            self.reader._cleanup()
        self.assertEqual(self.actions, [(1, 'start'), (1, 'finish'), (2, 'start'), (2, 'finish')])
        self.assertEqual(len(self.created), 2)
        self.assertTrue(all(p.is_closed() for p in self.created))

    def test_reserved_tabs_are_blocked_and_not_available_to_ordinary_selection_or_branch_detection(self):
        special = self.reader._new_segment(self.browser)
        key = special.page.evaluate('window.name')
        self.assertTrue(chatgpt.is_dictation_tab(key))
        ordinary = self.context.new_page()
        ordinary.goto('https://chatgpt.com/c/ordinary')
        ordinary.evaluate('(key) => window.name = key', chatgpt.CHAT_TAB_PREFIX + 'normal')
        special.page.bring_to_front()
        with self.assertRaisesRegex(chatgpt.ChatGPTError, 'transcript'):
            chatgpt._selected_chatgpt_tab(self.browser, key)
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt._dedicated_tab(self.browser, key)
        self.assertIs(chatgpt._current_chatgpt_tab(self.browser)[0], ordinary)
        self.assertEqual([value['page'] for value in _tab_snapshot(self.browser).values()], [ordinary])
        # Exercise the actual list scanner against this isolated browser.
        from unittest.mock import Mock
        fake = Mock()
        fake.chromium.connect_over_cdp.return_value = self.browser
        with patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.__enter__.return_value = fake
            tabs = chatgpt.list_chrome_chatgpt_tabs()
        self.assertEqual([t.key for t in tabs], [chatgpt.CHAT_TAB_PREFIX + 'normal'])
        self.reader._cleanup()
        self.assertFalse(ordinary.is_closed())

    def test_stop_before_start_does_not_turn_on_microphone(self):
        self.reader.stop()
        with self.assertRaises(dictation.Interrupted):
            self.reader._capture(self.browser)
        self.assertEqual(self.created, [])

    def test_shutdown_closes_owned_tabs_without_upload_or_touching_user_tabs(self):
        user = self.context.new_page()
        segment = self.reader._new_segment(self.browser)
        self.reader._begin(segment, self.browser)
        self.reader.shutdown()
        self.reader._cleanup()
        self.assertEqual(self.actions, [(1, 'start')])
        self.assertTrue(segment.page.is_closed())
        self.assertFalse(user.is_closed())

    def test_saved_text_is_cleared_before_closing_and_never_saved_twice(self):
        segment = self.reader._new_segment(self.browser)
        self.reader._begin(segment, self.browser)
        self.reader._finish(segment)
        self.assertEqual(segment.page.locator('#prompt-textarea').inner_text().strip(), '')
        self.reader._finish(segment)
        self.assertEqual(len([e for e in self.events if e.kind == 'final']), 1)
        self.reader._cleanup()

    def test_database_failure_does_not_clear_uncopied_draft_or_continue_recording(self):
        segment = self.reader._new_segment(self.browser)
        self.reader._begin(segment, self.browser)
        self.reader._emit = lambda e: False if e.kind == 'final' else None
        with self.assertRaisesRegex(chatgpt.ChatGPTError, 'database'):
            self.reader._finish(segment)
        self.assertEqual(segment.page.locator('#prompt-textarea').inner_text(), 'Transcript segment 1')
        self.reader._cleanup()

    def test_existing_draft_is_never_captured_or_sent(self):
        self.context.unroute('https://chatgpt.com/**')
        self.context.route('https://chatgpt.com/**', lambda r: r.fulfill(
            body=HTML.replace('contenteditable="true"></div>', 'contenteditable="true">User draft</div>'), content_type='text/html'))
        with self.assertRaises(chatgpt.ChatGPTError):
            self.reader._capture(self.browser)
        self.reader._cleanup()
        self.assertEqual(self.actions, [])


if __name__ == '__main__':
    unittest.main()
