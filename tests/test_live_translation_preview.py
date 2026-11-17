"""Preview lifecycle under continuously changing captions; no real browser calls."""
import threading
import time
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption.settings import Settings
from caption.transcriber import Event
from caption.translation_worker import Task
from caption.translation import Config, TranslationError
from caption.translation_store import TranslationStore
from caption.realtime_translation import RealtimePassage


class LivePreviewTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def test_submission_failure_keeps_capture_and_all_lines_in_database(self):
        self.enable()
        with patch.object(self.service.worker, 'submit_batch', side_effect=TranslationError('Original error')):
            for text in ('First captured sentence.', 'Second captured sentence.'):
                self.service.on_caption(Event('final', text, end_of_utterance=True))
            self.assertTrue(self.service.capture['active'])
            self.assertEqual(self.service.capture['error'], '')
            self.assertEqual(len(self.service.translations.lines(self.cid)), 2)
            self.assertEqual(self.service.state()['translation_error'], 'Original error')
            self.post('/api/capture/pause')
            self.assertEqual(self.service.capture['error'], '')

    def test_chrome_failure_does_not_pause_following_captions(self):
        self.enable()
        with patch.object(self.service.worker.translator, 'translate', side_effect=[TranslationError('Tab reloading'), 'Recovered']):
            self.service.on_caption(Event('final', 'First sentence.', end_of_utterance=True))
            self.wait_until(lambda: self.service.translation_issue == 'Tab reloading')
            self.assertTrue(self.service.capture['active'])
            self.service.on_caption(Event('final', 'Next sentence.', end_of_utterance=True))
            self.wait_until(lambda: any(row['last_state'] == 'chrome: done' for row in self.service.translations.lines(self.cid)))
            self.wait_until(lambda: not self.service.translation_issue)
            self.assertTrue(self.service.capture['active'])
            self.assertEqual(self.service.capture['error'], '')
            self.assertEqual(len(self.service.translations.lines(self.cid)), 2)

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
            self.assertEqual(self.service.translation_preview, 'Translated: I am learning today')
            self.service.on_caption(Event('partial', ''))
            self.wait_until(lambda: self.service.translations.reading(self.cid, Config.from_settings(Settings.load()))['realtime'][0]['text']
                            == 'Translated: I am learning today.')

    def test_live_history_survives_final_translation_toggle_and_store_reopen(self):
        self.enable()
        config = Config.from_settings(Settings.load())
        def live():
            return self.service.translations.reading(self.cid, config)['realtime']
        with patch.object(self.service.worker.translator, 'translate', side_effect=lambda text, *_: 'Live: ' + text), \
                patch.object(self.service.worker, 'submit_batch'):
            self.service.on_caption(Event('partial', 'First sentence. Second sentence.'))
            self.wait_until(lambda: len(live()) == 1)
            original_id = live()[0]['id']
            self.service.on_caption(Event('final', 'First sentence.', end_of_utterance=True))
            self.service.on_caption(Event('partial', 'Second sentence. Third sentence.'))
            self.wait_until(lambda: live()[0]['text'] == 'Live: First sentence. Second sentence. Third sentence.')
            self.assertEqual(live()[0]['id'], original_id)
            for text in ('Second sentence.', 'Third sentence.'):
                self.service.on_caption(Event('final', text, end_of_utterance=True))
            self.service.on_caption(Event('partial', ''))
            self.service.on_caption(Event('partial', 'Next passage.'))
            self.wait_until(lambda: len(live()) == 2)
            self.assertEqual(live()[1]['text'], 'Live: Next passage.')
            expected = live()
            for line in self.service.translations.lines(self.cid):
                self.service.translations.finish(self.service.translations.begin(line, config), 'done', 'Official answer')
            self.assertEqual(live(), expected)
            self.client.patch('/api/settings', json={'translation_partial': False}, headers=self.headers)
            self.assertEqual(live(), expected)
            reopened = TranslationStore(self.service.store.db_path)
            self.assertEqual(reopened.reading(self.cid, config)['realtime'], expected)
            self.assertEqual(reopened.reading(self.cid, Config(engine='chrome', target='fr'))['realtime'], [])
            other = self.service.store.create('Test', 'Other conversation')
            self.assertEqual(reopened.reading(other.id, config)['realtime'], [])
            response = self.client.get(f'/api/conversations/{self.cid}').json()
            self.assertEqual(response['translation_reading']['realtime'], expected)

    def test_continuous_passages_only_consume_confirmed_prefix(self):
        passage = RealtimePassage()
        text = 'A sentence with several words. ' * 40
        source = passage.source(text)
        self.assertLessEqual(len(source), passage.LIMIT + 10)
        passage.rendered_source = source
        self.assertFalse(passage.advance(text))
        passage.final(text)
        self.assertTrue(passage.advance('Still speaking'))
        self.assertEqual(source + ' ' + passage.committed, text.strip())
        self.assertTrue((passage.committed + ' Still speaking').startswith(passage.source('Still speaking')))

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
