import threading
import time
import unittest
from unittest.mock import patch

import test_research as fixtures
from caption.shared_access import BusyError


class ParallelResearchTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    post = fixtures.ResearchTests.post
    child = fixtures.ResearchTests.child
    wait_nodes = fixtures.ResearchTests.wait_nodes

    def wait_idle(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with self.service.lock:
                if not self.service.research.active:
                    return
            time.sleep(.02)
        self.fail('Workers did not finish')

    def test_parallel_tabs_cancel_one_keep_other_and_ignore_late_result(self):
        started = {tab: threading.Event() for tab in ('A', 'B')}
        release = {tab: threading.Event() for tab in started}
        calls = []
        def answer(prompt, attachments, on_status, **kw):
            tab = kw['selected_tab']
            calls.append(tab)
            kw['on_partial']('Partial ' + tab)
            started[tab].set()
            release[tab].wait(5)
            kw['on_partial']('Late ' + tab)
            return 'Final ' + tab

        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            try:
                a = self.child(config={'selected_tab': 'A'})[0]
                self.assertTrue(started['A'].wait(3))
                # A queued sibling must not prevent another tab from starting.
                same_tab = self.child(config={'selected_tab': 'A'})[0]
                b = self.child(config={'selected_tab': 'B'})[0]
                self.assertTrue(started['B'].wait(3))
                self.assertEqual(self.store.get(self.cid, same_tab['id'])['status'], 'queued')
                self.post(self.base + '/' + same_tab['id'] + '/cancel')
                cancelled = self.post(self.base + '/' + a['id'] + '/cancel')
                self.assertEqual(cancelled.status_code, 200)
                self.assertEqual(cancelled.json()['status'], 'cancelled')
                self.assertEqual(cancelled.json()['partial'], 'Partial A')
                self.assertFalse(self.service.job_cancel['research-' + b['id']].is_set())
                self.assertEqual(self.store.get(self.cid, b['id'])['status'], 'working')
                # Do not let a retry race the cancelled worker's late callbacks.
                self.assertEqual(self.post(self.base + '/' + a['id'] + '/retry').status_code, 409)
                release['A'].set()
                release['B'].set()
                self.wait_nodes([b])
                self.wait_idle()
                result = self.store.get(self.cid, a['id'])
                self.assertEqual((result['status'], result['partial'], result['answer']), ('cancelled', 'Partial A', ''))
                self.assertEqual(self.store.get(self.cid, b['id'])['answer'], 'Final B')
                self.assertEqual(calls, ['A', 'B'])
                self.post(self.base + '/' + a['id'] + '/retry')
                self.wait_nodes([a])
            finally:
                for event in release.values():
                    event.set()
                self.wait_idle()

    def test_busy_external_tab_does_not_starve_other_tabs(self):
        self.service.busy = True
        a = self.child(config={'selected_tab': 'external-busy'})[0]
        b = self.child(config={'selected_tab': 'free-tab'})[0]
        def answer(*args, **kw):
            if kw['selected_tab'] == 'external-busy':
                raise BusyError('Native owns this target')
            return 'Independent result'
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            self.service.busy = False
            self.wait_nodes([b])
            self.post(self.base + '/' + a['id'] + '/cancel')
            self.wait_idle()
        self.assertEqual(self.store.get(self.cid, b['id'])['answer'], 'Independent result')

    def test_shutdown_signals_every_active_node(self):
        started = {tab: threading.Event() for tab in ('A', 'B')}
        def answer(*args, **kw):
            started[kw['selected_tab']].set()
            kw['cancel_event'].wait(5)
            raise RuntimeError('Worker stopped')
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            nodes = [self.child(config={'selected_tab': tab})[0] for tab in started]
            for event in started.values():
                self.assertTrue(event.wait(3))
            self.service.research.close()
            self.wait_nodes(nodes, 'cancelled')
            self.assertFalse(self.service.research.active)
