import unittest
from unittest.mock import Mock, patch

from caption import settings
from caption.transcriber import Event
from caption.conversations import ConversationStore
import test_web_api as fixtures
from test_web_api import FakeReader


class DictationApiTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def save(self, **values):
        return self.client.patch('/api/settings', json=values, headers=self.headers)

    def test_capture_source_is_exclusive_and_dictation_text_is_persisted_and_translatable(self):
        self.assertEqual(self.save(web_capture_source='chatgpt').status_code, 200)
        self.assertEqual(settings.Settings.load().web_capture_source, 'chatgpt')
        with patch('web_api.service.chatgpt_dictation.ChatGPTDictationReader', side_effect=lambda emit, *a: FakeReader(emit)) as factory, \
             patch('web_api.service.livecaptions.LiveCaptionsReader') as windows:
            response = self.client.post(f'/api/conversations/{self.cid}/capture', headers={**self.headers, 'X-RTC-Tab': 'app-tab'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['capture']['source'], 'chatgpt')
            self.assertEqual(factory.call_args.args[1:], ('app-tab',))
            windows.assert_not_called()
            self.assertEqual(self.save(web_capture_source='windows').status_code, 400)
            self.assertEqual(self.post(f'/api/conversations/{self.cid}/capture').status_code, 400)
            self.service.on_caption(Event('final', 'Dictated text for the database.', True))
            self.post('/api/capture/pause')
        result = self.client.get(f'/api/conversations/{self.cid}').json()
        self.assertIn('Dictated text for the database.', result['transcript'])
        self.assertIn('ChatGPT Dictation', result['transcript'])
        self.assertEqual(result['lines'][-1]['source_text'], 'Dictated text for the database.')
        # Reopen the shared database independently; native sees the same conversation.
        self.assertIsNotNone(ConversationStore(self.service.store.db_path).get(self.cid))
        self.assertEqual(self.save(web_capture_source='windows').status_code, 200)
        self.assertEqual(self.post(f'/api/conversations/{self.cid}/capture').json()['capture']['source'], 'windows')
        self.post('/api/capture/pause')

    def test_invalid_settings_are_not_saved_and_new_conversation_uses_source_label(self):
        self.assertEqual(self.save(web_capture_source='windows').status_code, 200)
        for value in ('both', 'microphone', ''):
            self.assertEqual(self.save(web_capture_source=value).status_code, 400)
        self.assertEqual(self.save(chatgpt_dictation_interval=15).status_code, 422)
        self.assertEqual(self.save(chatgpt_dictation_overlap=True).status_code, 422)
        self.assertEqual(settings.Settings.load().web_capture_source, 'windows')
        self.save(web_capture_source='chatgpt')
        item = self.post('/api/conversations', {'title': 'Dictation'}).json()
        self.assertIn('ChatGPT', item['source'])

    def test_reader_failure_releases_capture_and_allows_restart(self):
        self.save(web_capture_source='chatgpt')
        with patch('web_api.service.chatgpt_dictation.ChatGPTDictationReader', side_effect=lambda emit, *a: FakeReader(emit)):
            self.post(f'/api/conversations/{self.cid}/capture')
            self.service.on_caption(Event('error', 'Microphone denied'))
            self.assertFalse(self.service.state()['capture']['active'])
            self.assertIn('Microphone denied', self.service.state()['capture']['error'])
            self.assertEqual(self.post(f'/api/conversations/{self.cid}/capture').status_code, 200)
            self.post('/api/capture/pause')

    def test_live_dictation_uses_existing_partial_and_final_translation_pipeline(self):
        self.save(web_capture_source='chatgpt', translation_enabled=True, translation_partial=True,
                  translation_engine='chrome', translation_source='en', translation_target='vi')
        with patch('web_api.service.chatgpt_dictation.ChatGPTDictationReader', side_effect=lambda emit, *a: FakeReader(emit)), \
             patch.object(self.service, 'schedule', return_value=Mock()), \
             patch.object(self.service.worker, 'preview') as preview, \
             patch.object(self.service.worker, 'submit_batch') as final:
            self.post(f'/api/conversations/{self.cid}/capture')
            self.service.on_caption(Event('partial', 'Speech still arriving'))
            self.service.send_preview()
            self.assertEqual(preview.call_args.args[1], 'Speech still arriving')
            self.service.on_caption(Event('final', 'Speech now stable.', True))
            final.assert_called_once()
            self.assertEqual(final.call_args.args[0][0]['source_text'], 'Speech now stable.')
            self.assertTrue(self.service.capture['active'])
            self.post('/api/capture/pause')


if __name__ == '__main__':
    unittest.main()
