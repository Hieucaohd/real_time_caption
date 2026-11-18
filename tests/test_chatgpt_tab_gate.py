"""No real Chrome/network: durable pacing, manual targets, cancellation and races."""
from contextlib import contextmanager, nullcontext
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from caption import chatgpt, chatgpt_tab_gate as gate, chrome_launcher, paths


def target(key, url='https://chatgpt.com/'):
    return {'targetId': key, 'type': 'page', 'url': url}


class TabGateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cache = Path(self.folder.name)
        self.patch = patch.object(paths, 'CACHE_DIR', self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now = 1000.0

    def advance(self, seconds, *_):
        self.now += seconds

    @contextmanager
    def clock(self):
        with patch.object(gate.time, 'time', side_effect=lambda: self.now), \
             patch.object(gate.time, 'monotonic', side_effect=lambda: self.now), \
             patch.object(gate, '_wait', side_effect=self.advance):
            yield

    def test_app_openings_wait_fifteen_seconds_and_persist_metadata(self):
        times = []
        with self.clock():
            for i in range(3):
                with gate.opening() as ticket:
                    ticket.target_id = str(i)
                    times.append(self.now)
        self.assertEqual(times, [1000, 1015, 1030])
        with gate._db() as db:
            self.assertEqual(db.execute('SELECT opened_at,target_id FROM last_open').fetchone(), (1030, '2'))

    def test_manual_tab_during_wait_restarts_cooldown_existing_tabs_do_not(self):
        browser = Mock()
        def observe(_):
            tabs = [target('manual-one')]
            if self.now >= 1005:
                tabs.append(target('manual-two', 'https://chatgpt.com/c/manual'))
            gate._observe_targets(tabs)
        with self.clock(), patch.object(gate, '_observe', side_effect=observe):
            with gate.opening(browser):
                self.assertEqual(self.now, 1020)

    def test_observer_uses_only_chatgpt_page_targets(self):
        tabs = [{'id': 'manual', 'type': 'page', 'url': 'https://chatgpt.com/g/project/c/abc'},
                {'id': 'google', 'type': 'page', 'url': 'https://translate.google.com/'},
                {'id': 'spoof', 'type': 'page', 'url': 'https://chatgpt.com.attacker.test/'},
                {'id': 'worker', 'type': 'worker', 'url': 'https://chatgpt.com/worker'}]
        with self.clock():
            for _ in range(2):
                with patch.object(gate.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(tabs).encode())):
                    gate.TabObserver().poll()
                self.now += 5
            self.assertEqual(gate._remaining(), 5)
        with gate._db() as db:
            self.assertEqual(db.execute('SELECT target_id FROM seen_tabs').fetchall(), [('manual',)])

    def test_owned_blank_tab_navigation_is_not_counted_twice(self):
        page = Mock(url='about:blank')
        page.context.new_cdp_session.return_value.send.return_value = {'targetInfo': {'targetId': 'own'}}
        with self.clock():
            with gate.opening() as ticket:
                ticket.opened(page)
            self.now += 7
            gate._observe_targets([target('own')])
            self.assertEqual(gate._remaining(), 8)

    def test_cancel_during_wait_preserves_timestamp_and_releases_lock(self):
        cancel = threading.Event()
        def stop(*args):
            cancel.set()
        with self.clock():
            gate._mark('previous')
            with patch.object(gate, '_wait', side_effect=stop):
                with self.assertRaises(chatgpt.ChatGPTCancelled):
                    with gate.opening(cancel=cancel):
                        self.fail('Cancelled opening must not create a tab')
            with gate._db() as db:
                self.assertEqual(db.execute('SELECT target_id FROM last_open').fetchone()[0], 'previous')
            cancel.clear()
            with gate.opening(cancel=cancel):
                self.assertEqual(self.now, 1015)

    def test_wait_holds_no_database_transaction(self):
        def check_db(seconds, *_):
            with gate._db() as db:
                db.execute("INSERT OR IGNORE INTO seen_tabs VALUES('probe')")
            self.advance(seconds)
        with self.clock():
            gate._mark()
            with patch.object(gate, '_wait', side_effect=check_db):
                with gate.opening():
                    self.assertEqual(self.now, 1015)

    def test_backward_wall_clock_is_bounded(self):
        with self.clock():
            gate._mark()
            self.now -= 3600
            with gate.opening():
                self.assertEqual(self.now, -2585)

    def test_separate_processes_share_spacing_and_survive_restart(self):
        code = '''import sys,time
from pathlib import Path
from caption import paths,chatgpt_tab_gate as gate
paths.CACHE_DIR=Path(sys.argv[1])
gate.MIN_INTERVAL=.35
with gate.opening():
    print(time.time(),flush=True)
'''
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.cache)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        times = []
        for process in processes:
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, err)
            times.append(float(out.strip()))
        times.sort()
        self.assertTrue(all(b - a >= .34 for a, b in zip(times, times[1:])), times)

    def test_google_tab_does_not_consume_chatgpt_slot(self):
        browser, page = Mock(), Mock()
        with patch.object(chatgpt, '_create_background_tab', return_value=page), patch.object(gate, 'opening') as opening:
            self.assertIs(chatgpt._open_background_tab(browser, chatgpt_tab=False), page)
            opening.assert_not_called()

    def test_regular_tab_creation_records_actual_target_inside_gate(self):
        browser, page, ticket = Mock(), Mock(), Mock()
        with patch.object(chatgpt, '_create_background_tab', return_value=page), \
             patch.object(gate, 'opening', return_value=nullcontext(ticket)) as opening:
            self.assertIs(chatgpt._open_background_tab(browser), page)
            opening.assert_called_once_with(browser, None, None)
            ticket.opened.assert_called_once_with(page)

    def test_ambiguous_create_target_error_does_not_open_duplicate_tab(self):
        browser = Mock(contexts=[Mock()])
        browser.contexts[0].expect_page.return_value = nullcontext(Mock())
        browser.new_browser_cdp_session.return_value.send.side_effect = TimeoutError('Lost acknowledgement')
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt._create_background_tab(browser)
        browser.contexts[0].new_page.assert_not_called()

    def test_launcher_enters_gate_before_starting_chrome(self):
        events = []
        @contextmanager
        def reservation(*args):
            events.append('enter')
            yield Mock()
            events.append('exit')
        with patch.object(chrome_launcher.urllib.request, 'urlopen', side_effect=OSError), \
             patch.object(Path, 'is_file', return_value=True), patch.object(paths, 'FROZEN', False), \
             patch.object(gate, 'opening', side_effect=reservation), \
             patch.object(chrome_launcher.subprocess, 'Popen', side_effect=lambda *a, **kw: events.append('spawn')):
            chrome_launcher.open_browser('http://127.0.0.1:8765')
        self.assertEqual(events, ['enter', 'spawn', 'exit'])

    def test_browser_observes_manual_target_and_background_creation(self):
        from playwright.sync_api import sync_playwright
        import time
        with sync_playwright() as p, patch.object(gate, 'MIN_INTERVAL', .4):
            # A persistent default context matches Chrome attached through 9222;
            # Target.createTarget without a browserContextId uses that context.
            context = p.chromium.launch_persistent_context(str(self.cache / 'chrome-fixture'), channel='chrome', headless=True)
            browser = context.browser
            try:
                context.route('**/*', lambda route: route.fulfill(body='<title>Fixture</title>'))
                manual = context.pages[0]
                manual.goto('https://chatgpt.com/')
                start = time.monotonic()
                created = chatgpt._open_background_tab(browser)
                self.assertGreaterEqual(time.monotonic() - start, .39)
                self.assertEqual(len(context.pages), 2)
                with gate._db() as db:
                    before = db.execute('SELECT opened_at FROM last_open').fetchone()[0]
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM seen_tabs').fetchone()[0], 2)
                created.goto('https://chatgpt.com/c/fixture')
                gate._observe(browser)
                with gate._db() as db:
                    self.assertEqual(db.execute('SELECT opened_at FROM last_open').fetchone()[0], before)
            finally:
                context.close()


if __name__ == '__main__':
    unittest.main()
