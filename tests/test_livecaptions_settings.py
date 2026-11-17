from contextlib import contextmanager
import unittest
import threading
from unittest.mock import MagicMock, Mock, patch

import test_web_api as fixtures
from caption import livecaptions_settings as captions
from caption.conversations import ConversationStore
from caption.transcriber import Event


class FakeControls:
    def __init__(self):
        self.language = 'English (United States)'
        self.available = [self.language, 'Japanese (Japan)']
        self.mic = True
        self.ready = True
        self.ignore_selection = False
        self.mic_calls = 0
        self.language_reads = 0
        self.continues = 0

    def languages(self, refresh=False):
        self.language_reads += 1
        return self.language, self.available

    def select_language(self, name):
        if self.ignore_selection:
            raise captions.CaptionsSettingsError('Windows did not select the language')
        self.language = name

    def continue_language(self):
        self.continues += 1
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
        def connected(_, **kwargs):
            yield controls
        with patch.object(captions, 'windows_controls', connected):
            yield

    def put(self, body):
        return self.client.put('/api/livecaptions/settings', headers=self.headers, json=body)

    def test_read_does_not_apply_or_save_and_does_not_launch_on_settings_get(self):
        with patch.object(captions, 'windows_controls') as windows:
            self.assertEqual(self.client.get('/api/livecaptions/settings').json(), {'microphone': None, 'language': ''})
            windows.assert_not_called()
        controls = FakeControls()
        with self.controller(controls):
            result = self.post('/api/livecaptions/inspect')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['microphone'])
        self.assertEqual(result.json()['languages'], controls.available)
        self.assertEqual(self.service.store.livecaptions_preferences(), {'microphone': None, 'language': ''})

    def test_explicit_apply_verifies_persists_and_can_reapply_saved_preferences(self):
        controls = FakeControls()
        desired = {'microphone': False, 'language': 'Japanese (Japan)'}
        self.service.capture['error'] = 'Không áp dụng được cài đặt Live Captions: previous menu failure'
        with self.controller(controls):
            response = self.put(desired)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.service.capture['error'], '')
            self.assertTrue(response.json()['ready'])
            self.assertFalse(controls.mic)
            self.assertEqual(controls.language_reads, 1)
            self.assertEqual(controls.continues, 1)
            reloaded = ConversationStore(self.service.store.db_path)
            self.assertEqual(reloaded.livecaptions_preferences(), desired)
            controls.language = 'English (United States)'; controls.mic = True
            captions.apply_windows(reloaded, reloaded.livecaptions_preferences())
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

    def test_create_start_resume_and_switch_never_automate_windows_settings(self):
        self.service.store.save_livecaptions_preferences({'microphone': True, 'language': 'Japanese (Japan)'})
        with patch.object(captions, 'windows_controls') as windows:
            for index in range(2):
                cid = self.post('/api/conversations', {'title': f'Conversation {index}'}).json()['id']
                for _ in range(2):
                    self.assertEqual(self.post(f'/api/conversations/{cid}/capture').status_code, 200)
                    self.assertFalse(hasattr(self.service.reader, 'configure'))
                    self.service.on_caption(Event('final', 'Caption keeps flowing.', end_of_utterance=True))
                    self.assertEqual(self.post('/api/capture/pause').status_code, 200)
                    self.assertEqual(self.service.capture['error'], '')
            windows.assert_not_called()

    def test_settings_failure_is_local_and_caption_reader_keeps_running(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        with patch.object(captions, 'apply_windows', side_effect=captions.CaptionsSettingsError('Microphone menu unavailable')):
            response = self.put({'microphone': True, 'language': ''})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.service.capture['active'])
        self.assertEqual(self.service.capture['error'], '')
        self.service.on_caption(Event('final', 'Still reading.', end_of_utterance=True))
        self.assertEqual(self.service.translations.lines(self.cid)[0]['source_text'], 'Still reading.')

    def test_nonfatal_warning_does_not_become_capture_error_or_paused_status(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        self.service.on_caption(Event('warning', 'Could not confirm settings'))
        self.assertEqual(self.service.capture['warning'], 'Could not confirm settings')
        self.assertEqual(self.service.capture['error'], '')
        self.assertTrue(self.service.capture['active'])
        self.post('/api/capture/pause')
        self.assertEqual(self.service.capture['status'], 'Paused')

    def test_real_capture_error_is_still_visible_and_stops_reader(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        self.service.on_caption(Event('error', 'Reader disconnected'))
        self.assertEqual(self.service.capture['error'], 'Reader disconnected')
        self.assertFalse(self.service.capture['active'])


class PreferenceRestoreTests(unittest.TestCase):
    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        idle = patch.object(captions, 'input_idle_seconds', return_value=100)
        self.patches.append(idle)
        idle.start()
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def test_restore_once_after_startup_and_each_reopened_window(self):
        wanted = {'microphone': True, 'language': 'Japanese (Japan)'}
        self.service.store.save_livecaptions_preferences(wanted)
        restorer = self.service.captions_preferences
        with patch.object(captions, 'window_session', return_value=(10, 20)) as session, \
                patch.object(captions.time, 'monotonic', return_value=0) as clock, \
                patch.object(captions, 'apply_windows', return_value={'ready': True}) as apply:
            restorer.poll()
            apply.assert_not_called()
            clock.return_value = 2
            for _ in range(5): restorer.poll()
            apply.assert_called_once_with(self.service.store, wanted, launch=False)
            for _ in range(2):
                cid = self.post('/api/conversations', {'title': 'Another'}).json()['id']
                self.post(f'/api/conversations/{cid}/capture'); self.post('/api/capture/pause')
                restorer.poll()
            self.assertEqual(apply.call_count, 1)
            session.return_value = None
            restorer.poll()
            session.return_value = (11, 21)
            restorer.poll()
            clock.return_value = 4
            restorer.poll(); restorer.poll()
            self.assertEqual(apply.call_count, 2)

    def test_no_window_or_no_saved_choices_never_calls_windows(self):
        restorer = self.service.captions_preferences
        with patch.object(captions, 'window_session', return_value=None) as session, \
                patch.object(captions.time, 'monotonic', return_value=0) as clock, \
                patch.object(captions, 'apply_windows') as apply:
            restorer.poll()
            session.return_value = (10, 20)
            restorer.poll(); clock.return_value = 2; restorer.poll()
            apply.assert_not_called()

    def test_background_restore_waits_until_user_stops_interacting(self):
        self.service.store.save_livecaptions_preferences({'microphone': True, 'language': ''})
        restorer = self.service.captions_preferences
        restorer._observed, restorer._seen_at = (10, 20), 0
        with patch.object(captions, 'window_session', return_value=(10, 20)), \
                patch.object(captions, 'input_idle_seconds', return_value=0) as idle, \
                patch.object(captions, 'apply_windows', return_value={'ready': True}) as apply:
            for _ in range(5): restorer.poll()
            apply.assert_not_called()
            self.assertIsNone(restorer._attempted)
            idle.return_value = 2
            restorer.poll(); restorer.poll()
            apply.assert_called_once()

    def test_failure_warns_once_without_stopping_capture_or_reopening_menus(self):
        self.service.store.save_livecaptions_preferences({'microphone': True, 'language': ''})
        self.post(f'/api/conversations/{self.cid}/capture')
        restorer = self.service.captions_preferences
        with patch.object(captions, 'window_session', return_value=(10, 20)), \
                patch.object(captions.time, 'monotonic', return_value=0) as clock, \
                patch.object(captions, 'apply_windows', side_effect=captions.CaptionsSettingsError('Not ready')) as apply:
            restorer.poll(); clock.return_value = 2
            for _ in range(5): restorer.poll()
            apply.assert_called_once()
            self.assertTrue(self.service.state()['capture']['warning'])
            self.assertEqual(self.service.capture['error'], '')
            self.assertTrue(self.service.capture['active'])
            self.service.on_caption(Event('final', 'Capture continues.', end_of_utterance=True))
            self.assertEqual(len(self.service.translations.lines(self.cid)), 1)

    def test_explicit_save_marks_session_and_background_does_not_repeat(self):
        restorer = self.service.captions_preferences
        self.service.store.save_livecaptions_preferences({'microphone': True, 'language': ''})
        with patch.object(captions, 'window_session', return_value=(10, 20)), \
                patch.object(captions, 'apply_windows', return_value={'ready': True}) as apply:
            restorer.save({'microphone': True, 'language': ''})
            restorer.poll(); restorer.poll()
            apply.assert_called_once()

    def test_slow_restore_does_not_lock_conversation_api_or_caption_events(self):
        restorer = self.service.captions_preferences
        self.service.store.save_livecaptions_preferences({'microphone': True, 'language': ''})
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set(); release.wait(3)
            return {'ready': True}
        with patch.object(captions, 'window_session', return_value=(10, 20)), \
                patch.object(captions, 'apply_windows', side_effect=slow) as apply:
            restorer._observed, restorer._seen_at = (10, 20), 0
            thread = threading.Thread(target=restorer.poll); thread.start()
            try:
                self.assertTrue(entered.wait(2))
                cid = self.post('/api/conversations', {'title': 'While restoring'}).json()['id']
                self.post(f'/api/conversations/{cid}/capture')
                self.service.on_caption(Event('final', 'Independent caption.', end_of_utterance=True))
                restorer.poll()  # Does not block or start a duplicate operation.
                self.assertEqual(len(self.service.translations.lines(cid)), 1)
                self.assertEqual(apply.call_count, 1)
            finally:
                release.set(); thread.join(3)


class MicrophoneControlTests(unittest.TestCase):
    def test_activation_uses_uia_when_windows_denies_foreground_request(self):
        windows = captions.WindowsControls(Mock())
        windows.window.ProcessId = 20
        windows.owns_focus = Mock(side_effect=[False, True])
        user32 = Mock()
        user32.GetForegroundWindow.return_value = 10
        with patch.object(captions.ctypes.windll, 'user32', user32):
            windows.open()
        windows.window.ButtonControl.return_value.SetFocus.assert_called_once()

    def test_activation_does_not_override_user_switch_during_open(self):
        windows = captions.WindowsControls(Mock())
        windows.window.ProcessId = 20
        windows.owns_focus = Mock(return_value=False)
        user32 = Mock()
        user32.GetForegroundWindow.side_effect = [10, 30]
        with patch.object(captions.ctypes.windll, 'user32', user32):
            with self.assertRaises(captions.FocusInterrupted):
                windows.open()
        windows.window.ButtonControl.assert_not_called()

    def test_cleanup_does_not_restore_old_window_after_user_switches_apps(self):
        controls = Mock(focus_process=20)
        controls.owns_focus.return_value = False
        user32 = Mock()
        user32.GetForegroundWindow.return_value = 10
        with patch.object(captions.ctypes.windll, 'user32', user32), \
                patch.object(captions, 'WindowsControls', return_value=controls), \
                patch.object(captions, 'ProcessLock'), \
                patch.dict('sys.modules', {'uiautomation': MagicMock()}):
            with captions.windows_controls('data.sqlite3'):
                pass
            controls.dismiss_menu.assert_not_called()
            user32.SetForegroundWindow.assert_not_called()
            controls.owns_focus.return_value = True
            with captions.windows_controls('data.sqlite3'):
                pass
            controls.dismiss_menu.assert_called_once()
            user32.SetForegroundWindow.assert_called_once_with(10)

    def test_focus_loss_stops_before_operating_controls(self):
        windows = captions.WindowsControls(Mock())
        windows.owns_focus = Mock(return_value=False)
        with self.assertRaises(captions.FocusInterrupted):
            windows.invoke('ButtonControl', 'SettingsButton')
        windows.window.ButtonControl.return_value.GetInvokePattern.assert_not_called()

    def test_interrupted_operation_waits_for_idle_then_retries_boundedly(self):
        store = Mock()
        operation = Mock(side_effect=[captions.FocusInterrupted('Changed windows'), {'ready': True}])
        with patch.object(captions, 'windows_controls') as controls, \
                patch.object(captions, 'input_idle_seconds', side_effect=[0, .5, 2]), \
                patch.object(captions.time, 'sleep') as sleep:
            self.assertEqual(captions._run_windows(store, operation), {'ready': True})
            self.assertEqual(controls.call_count, 2)
            self.assertEqual(sleep.call_count, 2)
        operation = Mock(side_effect=captions.FocusInterrupted('Changed windows'))
        with patch.object(captions, 'windows_controls'), \
                patch.object(captions, 'input_idle_seconds', return_value=2):
            with self.assertRaises(captions.FocusInterrupted):
                captions._run_windows(store, operation)
        self.assertEqual(operation.call_count, 3)

    def test_real_settings_error_is_not_retried(self):
        operation = Mock(side_effect=captions.CaptionsSettingsError('Unsupported language'))
        with patch.object(captions, 'windows_controls'):
            with self.assertRaises(captions.CaptionsSettingsError):
                captions._run_windows(Mock(), operation)
        operation.assert_called_once()

    def test_continuous_user_input_expires_without_reopening_the_menu(self):
        operation = Mock(side_effect=captions.FocusInterrupted('Changed windows'))
        with patch.object(captions, 'windows_controls'), \
                patch.object(captions, 'input_idle_seconds', return_value=0), \
                patch.object(captions.time, 'monotonic', side_effect=[0, 21]):
            with self.assertRaises(captions.FocusInterrupted):
                captions._run_windows(Mock(), operation)
        operation.assert_called_once()

    def test_background_restore_never_reopens_a_closed_window(self):
        windows = captions.WindowsControls(Mock())
        windows.window.Exists.return_value = False
        with patch.object(captions.subprocess, 'Popen') as launch:
            with self.assertRaises(captions.CaptionsSettingsError):
                windows.open(launch=False)
            launch.assert_not_called()

    def test_dismiss_does_not_reopen_menu_after_collapse_closed_it(self):
        windows = captions.WindowsControls(Mock())
        windows.exists = Mock(side_effect=[True, False])
        windows.control = Mock()
        windows.invoke = Mock()
        windows.dismiss_menu()
        windows.invoke.assert_not_called()

    def test_cleanup_failure_does_not_discard_confirmed_microphone_state(self):
        windows = captions.WindowsControls(Mock())
        mic = Mock(); mic.GetTogglePattern.return_value.ToggleState = 1
        windows.menu = Mock()
        windows.control = lambda kind, identity: mic if identity == 'MicrophoneMenuFlyoutItem' else Mock()
        windows.dismiss_menu = Mock(side_effect=RuntimeError('Menu disappeared'))
        self.assertTrue(windows.microphone(True))
        mic.GetTogglePattern.return_value.Toggle.assert_not_called()

    def test_language_cache_fingerprint_needs_no_app_session_registry_value(self):
        registry = MagicMock()
        registry.QueryValueEx.return_value = ('en-US', 1)
        with patch.dict('sys.modules', {'winreg': registry}), patch.object(captions, 'window_session', return_value=(10, 20)):
            self.assertEqual(captions.language_fingerprint(), ((10, 20), 'en-US'))
        self.assertEqual(registry.QueryValueEx.call_args.args[1], 'CaptionLanguage')
        self.assertEqual(registry.OpenKey.call_count, 1)

    def test_same_session_and_language_skip_picker_but_external_change_invalidates_cache(self):
        windows = captions.WindowsControls(Mock())
        windows.exists = Mock(return_value=False)
        windows.language_picker = Mock(side_effect=captions.CaptionsSettingsError('Picker requested'))
        with patch.object(captions, '_language_cache', (('session1', 'ja-JP'), 'Japanese (Japan)', ['Japanese (Japan)'])):
            with patch.object(captions, 'language_fingerprint', return_value=('session1', 'ja-JP')):
                self.assertEqual(windows.languages(), ('Japanese (Japan)', ['Japanese (Japan)']))
                windows.language_picker.assert_not_called()
                with self.assertRaises(captions.CaptionsSettingsError):
                    windows.languages(refresh=True)
            for fingerprint in (('session2', 'ja-JP'), ('session1', 'en-US'), None):
                with patch.object(captions, 'language_fingerprint', return_value=fingerprint):
                    with self.assertRaises(captions.CaptionsSettingsError):
                        windows.languages()

    def test_menu_invokes_once_and_waits_instead_of_repeated_toggling(self):
        windows = captions.WindowsControls(Mock())
        windows.exists = Mock(return_value=False)
        windows.invoke = Mock()
        windows.control = Mock(side_effect=[Mock(), captions.CaptionsSettingsError('Menu unavailable')])
        with self.assertRaises(captions.CaptionsSettingsError):
            windows.menu()
        windows.invoke.assert_called_once_with('ButtonControl', 'SettingsButton')

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
