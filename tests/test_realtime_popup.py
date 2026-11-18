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
        self.assertEqual(self.client.put(base, json={'opacity':65}).status_code, 403)
        for value in (0, 39, 101, '65'):
            self.assertEqual(self.client.put(base, headers=self.headers, json={'opacity':value}).status_code, 422)
        response = self.client.put(base, headers=self.headers, json={'opacity':65})
        self.assertEqual(response.status_code, 200)
        native.configure.assert_called_once_with(123, 65)
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


if __name__ == '__main__':
    unittest.main()
