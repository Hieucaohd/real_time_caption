import unittest
from unittest.mock import Mock

import test_web_api as fixtures


class RealtimePopupTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def test_popup_opacity_persists_without_changing_chat_settings_or_session(self):
        popup = self.service.realtime_popups
        native = Mock()
        native.valid.return_value = True
        popup.native = native
        popup.windows[self.cid] = dict(hwnd=123, owner=456)
        base = f'/api/conversations/{self.cid}/realtime-popup'
        original = self.service.realtime_chat.state(self.cid)['config']
        self.assertEqual(self.client.get(base).json()['opacity'], 100)
        self.assertEqual(self.client.get(base).json()['font_size'], 16)
        self.assertEqual(self.client.put(base + '/font-size', headers=self.headers, json={'font_size':24}).status_code, 200)
        self.assertEqual(self.client.put(base, json={'opacity':65}).status_code, 403)
        for value in (0, 39, 101, '65'):
            self.assertEqual(self.client.put(base, headers=self.headers, json={'opacity':value}).status_code, 422)
        response = self.client.put(base, headers=self.headers, json={'opacity':65})
        self.assertEqual(response.status_code, 200)
        native.configure.assert_called_once_with(123, 65)
        self.assertEqual(popup.opacity(), 65)
        self.assertEqual(popup.font_size(), 24)
        self.assertEqual(self.client.put(base + '/font-size', headers=self.headers, json={'font_size':20}).status_code, 200)
        self.assertEqual(popup.opacity(), 65)
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT opacity FROM realtime_popup_preferences').fetchone()[0], 65)
        self.assertEqual(self.service.realtime_chat.state(self.cid)['config'], original)
        self.assertEqual(self.client.delete(base, headers=self.headers).status_code, 200)
        native.close.assert_called_once_with(123)
        self.assertFalse(self.client.get(base).json()['visible'])

    def test_stale_window_handles_are_never_changed_or_closed(self):
        popup = self.service.realtime_popups
        popup.native = Mock()
        popup.native.valid.return_value = False
        popup.windows[self.cid] = dict(hwnd=123, owner=456)
        base = f'/api/conversations/{self.cid}/realtime-popup'
        self.assertEqual(self.client.put(base, headers=self.headers, json={'opacity':60}).status_code, 400)
        popup.hide(self.cid)
        popup.native.configure.assert_not_called()
        popup.native.close.assert_not_called()

    def test_open_existing_popup_reuses_window_and_shutdown_preserves_draft(self):
        popup = self.service.realtime_popups
        popup.native = Mock()
        popup.native.valid.return_value = True
        popup.windows[self.cid] = dict(hwnd=123, owner=456)
        base = f'/api/conversations/{self.cid}/realtime-popup'
        self.assertEqual(self.post(base).status_code, 200)
        popup.native.focus.assert_called_once_with(123)
        popup.close()
        popup.native.release.assert_called_once_with(123)
        popup.native.close.assert_not_called()

    def test_only_popup_document_allows_same_origin_embedding(self):
        self.assertEqual(self.client.get('/').headers['x-frame-options'], 'DENY')
        self.assertEqual(self.client.get('/?realtime_popup=' + self.cid).headers['x-frame-options'], 'SAMEORIGIN')
        self.assertEqual(self.client.get('/api/bootstrap').headers['x-frame-options'], 'DENY')

    def test_font_size_validation_and_persistence_without_native_window(self):
        base = f'/api/conversations/{self.cid}/realtime-popup'
        self.assertEqual(self.client.put(base + '/font-size', json={'font_size':24}).status_code, 403)
        for value in (0, -1, 33, '24', 20.5):
            self.assertEqual(self.client.put(base + '/font-size', headers=self.headers, json={'font_size':value}).status_code, 422)
        self.assertEqual(self.client.put(base + '/font-size', headers=self.headers, json={'font_size':1}).status_code, 200)
        self.assertEqual(self.client.get(base).json()['font_size'], 1)
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT font_size,opacity FROM realtime_popup_preferences').fetchone()[:], (1,100))
        self.assertEqual(self.client.put(base + '/font-size', headers=self.headers, json={'font_size':32}).status_code, 200)
        self.assertEqual(self.client.get(base).json()['font_size'], 32)
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT font_size,opacity FROM realtime_popup_preferences').fetchone()[:], (32,100))

    def test_legacy_popup_preferences_keep_opacity_during_migration(self):
        from web_api.realtime_popup import RealtimePopups
        self.service.realtime_popups.close()
        with self.service.store._connect() as db:
            db.execute('DROP TABLE realtime_popup_preferences')
            db.execute('CREATE TABLE realtime_popup_preferences(id INTEGER PRIMARY KEY, opacity INTEGER NOT NULL)')
            db.execute('INSERT INTO realtime_popup_preferences VALUES(1,65)')
        self.service.realtime_popups = RealtimePopups(self.service)
        state = self.service.realtime_popups.state(self.cid)
        self.assertEqual((state['font_size'], state['opacity']), (16,65))


if __name__ == '__main__':
    unittest.main()
