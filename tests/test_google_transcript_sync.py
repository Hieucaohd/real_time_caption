import threading
import time
import unittest
from unittest.mock import patch

import test_translation as fixtures
import test_web_api as api_fixtures
from caption.translation import Config
from caption.translation_store import TranslationStore
from caption.google_capture import GoogleCapture
from caption.transcriber import Event


class GoogleCaptureTests(unittest.TestCase):
    def test_partial_is_available_before_final_and_handoff_does_not_repeat(self):
        capture = GoogleCapture('conversation')
        capture.partial = 'Hello world and'
        self.assertEqual(capture.snapshot(), 'Hello world and')
        capture.final('Hello world')
        self.assertEqual(capture.snapshot(), 'Hello world and')
        capture.partial = 'and welcome'
        self.assertEqual(capture.snapshot(), 'Hello world and welcome')
        capture.final('and welcome')
        self.assertEqual(capture.snapshot(), 'Hello world and welcome')

    def test_repeated_spoken_words_are_retained_but_revisions_do_not_reset_source(self):
        capture = GoogleCapture('conversation')
        capture.partial = 'Yes'
        self.assertEqual(capture.snapshot(), 'Yes')
        capture.final('Yes'); capture.partial = 'Yes'
        self.assertEqual(capture.snapshot(), 'Yes Yes')
        capture.final('Yes'); capture.partial = 'I have a cat'
        old = capture.snapshot()
        capture.partial = 'I have a cap now'
        self.assertEqual(capture.snapshot(), old + ' now')


class GoogleSyncTests(unittest.TestCase):
    setUp = fixtures.TranslationPersistenceTests.setUp
    tearDown = fixtures.TranslationPersistenceTests.tearDown
    line = fixtures.TranslationPersistenceTests.line
    worker = fixtures.TranslationPersistenceTests.worker

    def test_pending_updates_coalesce_and_only_new_output_is_appended_in_database(self):
        config = Config(engine='google_web')
        entered, release = threading.Event(), threading.Event()
        calls = []
        def translate(text, *args, **kwargs):
            calls.append(text)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            return 'Translated: ' + text
        first = self.line('one', 'Hello')
        worker = self.worker()
        with patch('caption.google_translate.translate', side_effect=translate):
            worker.submit(first, config)
            self.assertTrue(entered.wait(3))
            second = self.line('two', 'World')
            worker.submit(second, config)
            third = self.line('three', 'Hello')
            worker.submit(third, config)
            self.assertEqual(len(worker._tasks), 1)
            release.set()
            self.assertEqual(worker.results.get(timeout=3)[1], 'done')
            self.assertEqual(worker.results.get(timeout=3)[1], 'done')
        self.assertEqual(calls, ['Hello', 'Hello\nWorld\nHello'])
        reopened = TranslationStore(self.store.db_path)
        reading = reopened.reading(self.item.id, config)
        self.assertEqual([s['text'] for s in reading['segments']], ['Translated: Hello\nWorld\nHello'])
        self.assertEqual(reading['translated_lines'], 3)
        self.assertEqual(reopened.missing(self.item.id, config), [])
        self.assertFalse(worker.submit(third, config))
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM realtime_translations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM line_translations').fetchone()[0], 0)

    def test_stop_does_not_overwrite_last_good_snapshot(self):
        config = Config(engine='google_web')
        self.line('one', 'Old')
        self.store.save_realtime(self.item.id, 'google-sync:' + config.fingerprint, config, 'Old', 'Saved')
        latest = self.line('two', 'New')
        entered, release = threading.Event(), threading.Event()
        def translate(*args, **kwargs):
            entered.set(); release.wait(3)
            return 'Must not be saved'
        worker = self.worker()
        with patch('caption.google_translate.translate', side_effect=translate):
            worker.submit(latest, config)
            self.assertTrue(entered.wait(3))
            worker.cancel_all(); release.set()
            self.assertEqual(worker.results.get(timeout=3)[1], 'cancelled')
        reading = self.store.reading(self.item.id, config)
        self.assertEqual(reading['segments'][0]['text'], 'Saved')
        self.assertEqual(reading['translated_lines'], 1)
        self.assertEqual(self.store.reading(self.item.id, Config(engine='google_web', target='ja'))['segments'], [])

    def test_old_output_is_immutable_when_google_rephrases_and_retry_does_not_duplicate(self):
        config = Config(engine='google_web')
        self.line('one', 'Hello.')
        self.store.append_google(self.item.id, config, 'Hello.', 'Xin chào.')
        self.line('two', 'How are you?')
        self.store.append_google(self.item.id, config, 'Hello.\nHow are you?', 'Chào bạn. Bạn khỏe không?')
        self.assertEqual(self.store.reading(self.item.id, config)['segments'][0]['text'], 'Xin chào. Bạn khỏe không?')
        reopened = TranslationStore(self.store.db_path)
        reopened.append_google(self.item.id, config, 'Hello.\nHow are you?', 'Chào bạn. Bạn khỏe không?')
        self.assertEqual(reopened.reading(self.item.id, config)['segments'][0]['text'], 'Xin chào. Bạn khỏe không?')
        reopened.append_google(self.item.id, config, 'Hello.\nHow are you?\nBye.', 'Chào bạn. Bạn khỏe không? Tạm biệt.')
        self.assertEqual(reopened.reading(self.item.id, config)['segments'][0]['text'], 'Xin chào. Bạn khỏe không? Tạm biệt.')

    def test_running_sync_picks_up_new_source_and_publishes_without_waiting_for_completion(self):
        config = Config(engine='google_web')
        first = self.line('one', 'One')
        entered, release, published, finish = (threading.Event() for _ in range(4))
        def translate(text, *args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            self.assertEqual(kwargs['latest'](), 'One\nTwo')
            kwargs['on_result']('First translated. Second translated.')
            published.set()
            self.assertTrue(finish.wait(3))
            return 'First translated. Second translated.'
        worker = self.worker()
        with patch('caption.google_translate.translate', side_effect=translate) as run:
            worker.submit(first, config)
            self.assertTrue(entered.wait(3))
            second = self.line('two', 'Two')
            worker.submit(second, config)
            release.set()
            self.assertTrue(published.wait(3))
            self.assertEqual(self.store.reading(self.item.id, config)['segments'][0]['text'], 'First translated. Second translated.')
            self.assertEqual(self.store.google_source(self.item.id, config), '')
            finish.set()
            self.assertEqual(worker.results.get(timeout=3)[1], 'done')
            run.assert_called_once()
        self.assertEqual(self.store.google_source(self.item.id, config), 'One\nTwo')


class GoogleSyncApiTests(unittest.TestCase):
    setUp = api_fixtures.WebApiTests.setUp
    tearDown = api_fixtures.WebApiTests.tearDown
    post = api_fixtures.WebApiTests.post

    def test_live_sync_ignores_batch_count_and_manual_sync_stays_in_current_session(self):
        self.client.patch('/api/settings', headers=self.headers, json={
            'translation_engine': 'google_web', 'translation_enabled': True,
            'translation_batch_size': 50, 'translation_batch_timeout': 300})
        self.service.capture['conversation_id'] = self.cid
        self.service.google_capture = GoogleCapture(self.cid)
        self.service.google_capture.partial = 'New words'
        with patch.object(self.service.worker, 'sync_google', return_value=True) as sync, \
             patch.object(self.service.batcher, 'add') as batch:
            self.service.on_lines([('one', 'now', 'Hello'), ('two', 'now', 'World')])
            sync.assert_not_called()  # Indexing existing lines is not a live-source event.
            self.service.send_google()
            self.assertEqual(sync.call_args.kwargs['text'], 'New words')
            batch.assert_not_called()
            result = self.post('/api/conversations/' + self.cid + '/translate', {'ids': [1], 'force': True})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['lines'], 1)
            self.assertTrue(sync.call_args.kwargs['force'])

    def test_start_excludes_history_partial_translates_and_restart_does_not_replay(self):
        transcript = self.service.transcript_for(self.cid)
        transcript.begin_segment(); transcript.write('Historical sentence.', True); transcript.close()
        self.client.patch('/api/settings', headers=self.headers, json={
            'translation_engine': 'google_web', 'translation_enabled': True})
        sent = threading.Event()
        def translate(text, *a, **kw):
            sent.set()
            return 'Translated ' + text
        with patch('caption.google_translate.translate', side_effect=translate) as run:
            self.post('/api/conversations/' + self.cid + '/capture')
            first_session = self.service.google_capture.key
            self.service.on_caption(Event('partial', 'Currently speaking'))
            self.assertTrue(sent.wait(3), 'A partial must be sent before any final event')
            self.assertEqual(run.call_args.args[0], 'Currently speaking')
            self.service.on_caption(Event('final', 'Currently speaking', True))
            self.service.on_caption(Event('partial', ''))
            self.post('/api/capture/pause')
            deadline = time.monotonic() + 3
            while not self.service.translations.google_source(self.cid, Config.from_settings(api_fixtures.settings.Settings.load()), first_session):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            self.post('/api/conversations/' + self.cid + '/capture')
            self.assertNotEqual(self.service.google_capture.key, first_session)
            self.assertEqual(self.service.google_capture.text, '')
            sent.clear()
            self.service.on_caption(Event('partial', 'Only new speech'))
            self.assertTrue(sent.wait(3))
            self.assertEqual(run.call_args.args[0], 'Only new speech')
            self.service.on_caption(Event('final', 'Only new speech', True))
            self.service.on_caption(Event('partial', ''))
            self.post('/api/capture/pause')
            deadline = time.monotonic() + 3
            config = Config.from_settings(api_fixtures.settings.Settings.load())
            while len(self.service.translations.reading(self.cid, config)['segments']) < 2:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            reading = TranslationStore(self.service.store.db_path).reading(self.cid, config)
            self.assertEqual([s['text'] for s in reading['segments']], ['Translated Currently speaking', 'Translated Only new speech'])
            historical = self.service.translations.lines(self.cid)[0]['id']
            self.assertTrue(all(historical not in s['line_ids'] for s in reading['segments']))
            self.assertTrue(all('Historical' not in call.args[0] for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
