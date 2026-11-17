"""Real browser controls with simulated live text; never use a physical microphone."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from caption import chatgpt, chatgpt_dictation as dictation, paths
from caption.chatgpt_branch import _tab_snapshot
from caption.dictation_text import DictationText


HTML = r'''<form onsubmit="event.preventDefault(); window.report('SEND')">
<div id="prompt-textarea" contenteditable="true"></div>
<div id="controls"><button type="button" aria-label="Dictate" onclick="begin()">Mic</button></div>
<button type="submit" data-testid="send-button" aria-label="Send">Send</button>
<button type="button" aria-label="Start Voice" onclick="window.report('VOICE')">Voice</button>
</form><script>
function begin() {
  window.report('start');
  controls.innerHTML = '<button type="button" aria-label="Cancel" onclick="window.report(\'cancel\')">X</button><button type="button" aria-label="Stop dictation" onclick="finish()">Stop</button>';
}
function finish() {
  window.report('stop');
  controls.innerHTML = '<button type="button" aria-label="Dictate" onclick="begin()">Mic</button>';
}
</script>'''


class DictationTextTests(unittest.TestCase):
    def test_pending_revisions_are_replaced_and_final_is_never_duplicated(self):
        stream = DictationText()
        self.assertEqual(stream.update('We need won', 0), ([], 'We need won'))
        self.assertEqual(stream.update('We need one.', .2), ([], 'We need one.'))
        self.assertEqual(stream.update('We need one. Next', 1.1), (['We need one.'], 'Next'))
        self.assertEqual(stream.update('We need one. Next word', 1.2), ([], 'Next word'))
        self.assertEqual(stream.update('We need one. Next word', 3), (['Next word'], ''))
        self.assertEqual(stream.update('We need one. Next word', 5, flush=True), ([], ''))

    def test_repeated_real_speech_is_retained_and_existing_draft_is_not_new_speech(self):
        stream = DictationText('Old draft')
        self.assertEqual(stream.update('Old draft Yes.', 1, flush=True), (['Yes.'], ''))
        self.assertEqual(stream.update('Old draft Yes. Yes.', 2, flush=True), (['Yes.'], ''))

    def test_revision_before_committed_boundary_does_not_replay_prefix(self):
        stream = DictationText()
        stream.update('Hello world.', 0, flush=True)
        self.assertEqual(stream.update('Hello World. New words', 1, flush=True), (['New words'], ''))

    def test_empty_snapshot_flushes_cached_tail_and_replaced_buffer_can_continue(self):
        stream = DictationText()
        stream.update('Last words', 0)
        self.assertEqual(stream.update('', .2, flush=True), (['Last words'], ''))
        self.assertEqual(stream.update('Completely different', .3, flush=True), (['Completely different'], ''))

    def test_continuous_speech_reaches_database_without_stopping_microphone(self):
        stream = DictationText()
        stream.update('First.', 0)
        stream.update('First. Second.', .3)
        stream.update('First. Second. Third.', .6)
        self.assertEqual(stream.update('First. Second. Third. Fourth.', .9), (['First.'], 'Second. Third. Fourth.'))

    def test_unpunctuated_and_non_latin_text(self):
        stream = DictationText()
        words = ' '.join(f'word{i}' for i in range(50))
        stream.update(words, 0)
        completed, tail = stream.update(words + ' new', 1)
        self.assertEqual(' '.join([*completed, tail]), words + ' new')
        self.assertTrue(completed)
        stream = DictationText()
        stream.update('こんにちは。次', 0)
        self.assertEqual(stream.update('こんにちは。次の文', 1), (['こんにちは。'], '次の文'))
        self.assertEqual(stream.update('こんにちは。次の文', 2, flush=True), (['次の文'], ''))


class DictationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.p.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.context = self.browser.new_context(); self.addCleanup(self.context.close)
        self.context.route('https://chatgpt.com/**', lambda r: r.fulfill(body=HTML, content_type='text/html'))
        self.events, self.actions, self.created = [], [], []
        self.reader = dictation.ChatGPTDictationReader(self.events.append)
        def new_page(_):
            self.assertFalse(self.created, 'A capture session must use only one tab')
            page = self.context.new_page(); self.created.append(page)
            page.expose_function('report', lambda action: self.actions.append(action))
            return page
        for context in (patch.object(paths, 'CACHE_DIR', Path(self.temp.name)),
                        patch.object(chatgpt, '_open_background_tab', side_effect=new_page)):
            context.start(); self.addCleanup(context.stop)

    def open_and_begin(self):
        page = self.reader._open_page(self.browser)
        self.reader._begin(page, self.browser)
        return page

    def test_live_text_and_corrections_are_read_without_stopping_or_clearing_editor(self):
        page = self.open_and_begin()
        page.evaluate('''() => {
            const editor = document.querySelector('#prompt-textarea');
            editor.innerText = 'This is rong';
            setTimeout(() => editor.innerText = 'This is right.', 250);
            setTimeout(() => editor.innerText = 'This is right. More words', 500);
        }''')
        timer = threading.Timer(1.5, self.reader.stop); timer.start()
        try:
            self.reader._monitor()
            self.assertEqual(self.actions, ['start'])
            self.assertEqual(page.locator('#prompt-textarea').inner_text(), 'This is right. More words')
            self.assertTrue(any(e.kind == 'partial' and e.text == 'This is rong' for e in self.events))
            self.assertIn('This is right.', [e.text for e in self.events if e.kind == 'final'])
        finally:
            timer.cancel(); self.reader._cleanup()
        self.assertEqual(self.actions, ['start', 'stop'])
        self.assertEqual([e.text for e in self.events if e.kind == 'final'], ['This is right.', 'More words'])
        self.assertEqual(len(self.created), 1)
        self.assertTrue(page.is_closed())

    def test_pause_flushes_pending_words_exactly_once(self):
        page = self.open_and_begin()
        page.locator('#prompt-textarea').fill('Unfinished words')
        self.reader._read_live()
        self.assertEqual([e.text for e in self.events if e.kind == 'partial'], ['Unfinished words'])
        self.reader.stop(); self.reader._cleanup(); self.reader._cleanup()
        self.assertEqual([e.text for e in self.events if e.kind == 'final'], ['Unfinished words'])
        self.assertEqual(self.actions, ['start', 'stop'])

    def test_closed_tab_keeps_last_observed_words(self):
        page = self.open_and_begin()
        page.locator('#prompt-textarea').fill('Last observed words')
        self.reader._read_live(); page.close()
        with self.assertRaises(chatgpt.ChatGPTError):
            self.reader._monitor()
        self.reader._cleanup()
        self.assertEqual([e.text for e in self.events if e.kind == 'final'], ['Last observed words'])

    def test_existing_and_late_restored_text_do_not_block_recording(self):
        page = self.reader._open_page(self.browser)
        page.locator('#prompt-textarea').fill('Existing draft')
        self.reader._begin(page, self.browser)
        self.assertEqual(self.actions, ['start'])
        page.locator('#prompt-textarea').fill('Existing draft New microphone text')
        self.reader._read_live()
        self.reader.shutdown(); self.reader._cleanup()
        self.assertEqual([e.text for e in self.events if e.kind == 'final'], ['New microphone text'])

    def test_reserved_tab_cannot_receive_questions_or_be_detected_as_a_new_branch(self):
        page = self.reader._open_page(self.browser)
        key = page.evaluate('window.name')
        ordinary = self.context.new_page(); ordinary.goto('https://chatgpt.com/c/ordinary')
        ordinary.evaluate('(key) => window.name = key', chatgpt.CHAT_TAB_PREFIX + 'normal')
        page.bring_to_front()
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt._selected_chatgpt_tab(self.browser, key)
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt._dedicated_tab(self.browser, key)
        self.assertIs(chatgpt._current_chatgpt_tab(self.browser)[0], ordinary)
        self.assertEqual([v['page'] for v in _tab_snapshot(self.browser).values()], [ordinary])
        fake = Mock(); fake.chromium.connect_over_cdp.return_value = self.browser
        with patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.__enter__.return_value = fake
            self.assertEqual([t.key for t in chatgpt.list_chrome_chatgpt_tabs()], [chatgpt.CHAT_TAB_PREFIX + 'normal'])
        self.reader._cleanup(); self.assertFalse(ordinary.is_closed())

    def test_stop_before_start_never_opens_microphone(self):
        self.reader.stop()
        with self.assertRaises(dictation.Interrupted):
            self.reader._capture(self.browser)
        self.assertEqual(self.created, [])

    def test_disabled_dictate_waits_until_enabled(self):
        page = self.reader._open_page(self.browser)
        page.evaluate('''() => {const b=document.querySelector('[aria-label="Dictate"]');
            b.disabled=true; setTimeout(()=>b.disabled=false, 2500);}''')
        self.reader._begin(page, self.browser)
        self.assertEqual(self.actions, ['start'])
        self.reader.shutdown(); self.reader._cleanup()

    def test_disabled_timeout_is_friendly_and_pause_interrupts_wait(self):
        page = self.reader._open_page(self.browser)
        page.locator('[aria-label="Dictate"]').evaluate('b => b.disabled=true')
        with patch.object(dictation, 'START_READY_TIMEOUT', .2), self.assertRaisesRegex(chatgpt.ChatGPTError, 'chưa sẵn sàng'):
            self.reader._begin(page, self.browser)
        timer = threading.Timer(.2, self.reader.stop); timer.start()
        try:
            with self.assertRaises(dictation.Interrupted):
                self.reader._begin(page, self.browser)
        finally:
            timer.cancel(); self.reader._cleanup()
        self.assertEqual(self.actions, [])

    def test_browser_error_details_are_logged_not_shown(self):
        from playwright.sync_api import TimeoutError
        fake = Mock(); fake.chromium.connect_over_cdp.return_value = self.browser
        with patch('playwright.sync_api.sync_playwright') as factory, \
             patch.object(self.reader, '_capture', side_effect=TimeoutError('Locator.click: internal selector')):
            factory.return_value.__enter__.return_value = fake
            with self.assertLogs('caption.chatgpt_dictation', level='ERROR') as logged:
                self.reader._run()
        message, = [e.text for e in self.events if e.kind == 'error']
        self.assertNotIn('internal selector', message)
        self.assertIn('internal selector', '\n'.join(logged.output))
        self.assertEqual(self.events[-1].kind, 'stopped')


if __name__ == '__main__':
    unittest.main()
