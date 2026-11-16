"""Preview lifecycle under continuously changing captions; no real browser calls."""
import threading
import time
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption.settings import Settings
from caption.transcriber import Event
from caption.translation_worker import Task
from caption.translation import Config


class LivePreviewTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def enable(self, engine='chrome'):
        current = Settings.load()
        current.translation_engine = engine
        current.translation_enabled = current.translation_partial = True
        current.save()
        self.post(f'/api/conversations/{self.cid}/capture')

    def wait_until(self, check):
        deadline = time.monotonic() + 4
        while not check():
            if time.monotonic() > deadline: self.fail('Timed out waiting for preview')
            time.sleep(.02)

    def test_continuous_words_do_not_cancel_active_preview_or_clear_visible_result(self):
        self.enable()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def translate(text, config, cancel):
            calls.append((text, cancel))
            entered.set(); release.wait(3)
            return 'Translated: ' + text
        with patch.object(self.service.worker.translator, 'translate', side_effect=translate):
            try:
                self.service.on_caption(Event('partial', 'I am'))
                self.assertTrue(entered.wait(2))
                revision = self.service.preview_revision
                self.service.translation_preview = 'Existing draft'
                self.service.on_caption(Event('partial', 'I am learning'))
                self.service.on_caption(Event('partial', 'I am learning today'))
                self.assertEqual(self.service.preview_revision, revision)
                self.assertFalse(calls[0][1].is_set())
                self.assertEqual(self.service.translation_preview, 'Existing draft')
            finally:
                release.set()
            self.wait_until(lambda: self.service.translation_preview == 'Translated: I am learning today')
            self.assertEqual(self.service.translations.lines(self.cid), [])
            self.service.on_caption(Event('final', 'I am learning today.', end_of_utterance=True))
            self.wait_until(lambda: any(row['last_state'] == 'chrome: done'
                                       for row in self.service.translations.lines(self.cid)))
            self.assertEqual(self.service.translation_preview, '')

    def test_cloud_engine_and_disabled_preview_do_not_queue_any_draft(self):
        self.enable('google_web')
        with patch.object(self.service.worker, 'preview') as preview:
            self.service.on_caption(Event('partial', 'Still speaking'))
            self.assertIsNone(self.service.preview_timer)
            self.assertIsNone(self.service.preview_snapshot)
            current = Settings.load(); current.translation_engine = 'chrome'; current.translation_partial = False; current.save()
            self.service.on_caption(Event('partial', 'More words'))
            self.assertIsNone(self.service.preview_timer)
            preview.assert_not_called()

    def test_turning_off_cancels_pending_timer_and_discards_late_result(self):
        self.enable()
        self.service.on_caption(Event('partial', 'Unfinished'))
        revision = self.service.preview_revision
        self.service.translation_preview = 'Draft'
        result = self.client.patch('/api/settings', json={'translation_partial': False}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(self.service.preview_timer)
        self.assertIsNone(self.service.preview_snapshot)
        self.assertEqual(self.service.translation_preview, '')
        stale = Task({'conversation_id': self.cid, 'source_text': 'Unfinished'}, Config(engine='chrome'),
                     preview=True, revision=revision)
        self.service.worker.results.put((stale, 'done', 'Late draft'))
        self.wait_until(lambda: self.service.worker.results.empty())
        self.assertEqual(self.service.translation_preview, '')

    def test_turning_on_uses_current_partial_without_waiting_for_next_word(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        self.service.on_caption(Event('partial', 'Already speaking'))
        with patch.object(self.service.worker.translator, 'translate', return_value='Live translation'):
            response = self.client.patch('/api/settings', json={
                'translation_engine': 'chrome', 'translation_enabled': True, 'translation_partial': True,
            }, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.wait_until(lambda: self.service.translation_preview == 'Live translation')
            self.post('/api/capture/pause')
            self.assertEqual(self.service.translation_preview, '')


if __name__ == '__main__':
    unittest.main()
