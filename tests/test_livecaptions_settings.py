from contextlib import contextmanager
import unittest
from unittest.mock import Mock, patch

import test_web_api as fixtures
from caption import livecaptions_settings as captions
from caption.conversations import ConversationStore
from caption.livecaptions import LiveCaptionsReader


class FakeControls:
    def __init__(self):
        self.language = 'English (United States)'
        self.available = [self.language, 'Japanese (Japan)']
        self.mic = True
        self.ready = True
        self.ignore_selection = False
        self.mic_calls = 0

    def languages(self):
        return self.language, self.available

    def select_language(self, name):
        if not self.ignore_selection:
            self.language = name

    def continue_language(self):
        return self.ready

    def microphone(self, desired=None):
        self.mic_calls += 1
        if desired is not None:
            self.mic = desired
        return self.mic


class LiveCaptionsSettingsTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    @contextmanager
    def controller(self, controls):
        @contextmanager
        def connected(_):
            yield controls
        with patch.object(captions, 'windows_controls', connected):
            yield

    def put(self, body):
        return self.client.put('/api/livecaptions/settings', headers=self.headers, json=body)

    def test_read_does_not_apply_or_save_and_does_not_launch_on_settings_get(self):
        with patch.object(captions, 'windows_controls') as windows:
            self.assertEqual(self.client.get('/api/livecaptions/settings').json(), {'microphone': None, 'language': ''})
            captions.apply_saved(self.service.store)
            windows.assert_not_called()
        controls = FakeControls()
        with self.controller(controls):
            result = self.post('/api/livecaptions/inspect')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['microphone'])
        self.assertEqual(result.json()['languages'], controls.available)
        self.assertEqual(self.service.store.livecaptions_preferences(), {'microphone': None, 'language': ''})

    def test_apply_verifies_persists_and_reapplies_after_reopen(self):
        controls = FakeControls()
        desired = {'microphone': False, 'language': 'Japanese (Japan)'}
        with self.controller(controls):
            response = self.put(desired)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['ready'])
            self.assertFalse(controls.mic)
            reloaded = ConversationStore(self.service.store.db_path)
            self.assertEqual(reloaded.livecaptions_preferences(), desired)
            controls.language = 'English (United States)'; controls.mic = True
            captions.apply_saved(reloaded)
            self.assertEqual(controls.language, desired['language'])
            self.assertFalse(controls.mic)

    def test_unsupported_or_unconfirmed_language_does_not_save_or_toggle_mic(self):
        controls = FakeControls()
        with self.controller(controls):
            response = self.put({'microphone': False, 'language': 'Unsupported'})
            self.assertEqual(response.status_code, 400)
            controls.ignore_selection = True
            response = self.put({'microphone': False, 'language': 'Japanese (Japan)'})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(controls.mic_calls, 0)
        self.assertEqual(self.service.store.livecaptions_preferences()['language'], '')

    def test_download_pending_saves_intent_without_claiming_applied(self):
        controls = FakeControls(); controls.ready = False
        desired = {'microphone': False, 'language': 'Japanese (Japan)'}
        with self.controller(controls):
            response = self.put(desired)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['ready'])
            self.assertEqual(response.json()['status'], 'setup_required')
            self.assertIsNone(response.json()['microphone'])
            with self.assertRaises(captions.CaptionsSettingsError):
                captions.apply_saved(self.service.store)
        self.assertEqual(controls.mic_calls, 0)
        self.assertEqual(self.service.store.livecaptions_preferences(), desired)

    def test_invalid_and_unauthorized_requests_do_not_touch_windows(self):
        with patch.object(captions, 'windows_controls') as windows:
            self.assertEqual(self.client.post('/api/livecaptions/inspect').status_code, 403)
            self.assertEqual(self.client.put('/api/livecaptions/settings', json={}).status_code, 403)
            for body in ({'microphone': 'false'}, {'language': 'x' * 201}, {'unknown': True}):
                self.assertEqual(self.put(body).status_code, 422)
            windows.assert_not_called()

    def test_microphone_only_keeps_current_language(self):
        controls = FakeControls()
        with self.controller(controls):
            response = self.put({'microphone': False, 'language': ''})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(controls.language, 'English (United States)')
        self.assertFalse(controls.mic)

    def test_reader_runs_saved_preferences_before_attaching(self):
        reader = LiveCaptionsReader(lambda _: None)
        events = []
        reader.configure = lambda: events.append('configure')
        def attach(*args, **kwargs):
            events.append('attach'); reader.stop()
        with patch.object(reader, '_find_text', side_effect=attach):
            reader._loop(object())
        self.assertEqual(events, ['configure', 'attach'])


class MicrophoneControlTests(unittest.TestCase):
    def test_never_blindly_toggles_and_verifies_windows_state(self):
        windows = captions.WindowsControls(Mock())
        mic = Mock(); mic.GetTogglePattern.return_value.ToggleState = 1
        pref = Mock()
        windows.menu = Mock(); windows.dismiss_menu = Mock()
        windows.control = lambda kind, identity: mic if identity == 'MicrophoneMenuFlyoutItem' else pref
        self.assertTrue(windows.microphone(True))
        mic.GetTogglePattern.return_value.Toggle.assert_not_called()
        with patch.object(captions.time, 'sleep'):
            with self.assertRaises(captions.CaptionsSettingsError):
                windows.microphone(False)  # Simulate Windows ignoring the toggle.
        mic.GetTogglePattern.return_value.Toggle.assert_called_once()
