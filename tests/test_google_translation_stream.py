import unittest

from starlette.websockets import WebSocketDisconnect

import test_web_api as fixtures
from caption.translation import Config
from caption.settings import Settings


class GoogleTranslationStreamTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def test_push_rewrites_and_reconnect_reads_latest_persisted_snapshot(self):
        self.client.patch('/api/settings', headers=self.headers, json={'translation_engine': 'google_web'})
        config = Config.from_settings(Settings.load())
        store = self.service.translations
        store.append_google(self.cid, config, 'Earlier source', 'Earlier translation.', 'earlier')
        url = f'/api/conversations/{self.cid}/translation-stream'
        with self.client.websocket_connect(url, headers={'origin': 'http://testserver'}) as socket:
            self.assertEqual(socket.receive_json()['reading']['segments'][0]['text'], 'Earlier translation.')
            for text in ['Initial words', 'A completely different sentence', 'Short', '']:
                store.append_google(self.cid, config, 'Source', text, 'current')
                self.service.worker.on_google_update(self.cid, config)
                frame = socket.receive_json()
                self.assertEqual(frame['conversation_id'], self.cid)
                self.assertEqual([s['text'] for s in frame['reading']['segments']], ['Earlier translation.', text])
        self.assertFalse(self.service.translation_updates.listeners)
        store.append_google(self.cid, config, 'Source', 'Saved while disconnected', 'current')
        with self.client.websocket_connect(url, headers={'origin': 'http://testserver'}) as socket:
            self.assertEqual(socket.receive_json()['reading']['segments'][-1]['text'], 'Saved while disconnected')

    def test_foreign_or_missing_origin_cannot_read_translations(self):
        for headers in [{}, {'origin': 'https://example.com'}, {'origin': 'http://testserver.evil'}]:
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(f'/api/conversations/{self.cid}/translation-stream', headers=headers):
                    self.fail('Should reject foreign origins')
