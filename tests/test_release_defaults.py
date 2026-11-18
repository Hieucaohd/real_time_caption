"""Release defaults, saved overrides, and bundle/data separation."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from caption import defaults, paths, settings
import test_web_api as fixtures


class ReleaseDefaultsTests(unittest.TestCase):
    post = fixtures.WebApiTests.post

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.bundle = self.root / 'bundle'
        (self.bundle / 'config').mkdir(parents=True)
        self.document = json.loads((paths.BUNDLE_DIR / 'config/defaults.json').read_text(encoding='utf-8'))
        self.override = patch.object(paths, 'BUNDLE_DIR', self.bundle)
        self.override.start()
        self.write()

    def tearDown(self):
        fixtures.WebApiTests.tearDown(self)
        self.override.stop()
        defaults._load.cache_clear()

    def write(self):
        (self.bundle / 'config/defaults.json').write_text(json.dumps(self.document), encoding='utf-8')
        defaults.check()

    def test_new_settings_use_bundle_and_explicit_constructor_values_win(self):
        self.document['settings'].update(chat_chatgpt_power=2, summary_max_lines=17, translation_engine='chrome')
        self.write()
        settings.SETTINGS_PATH.unlink()
        new = settings.Settings.load()
        self.assertEqual((new.chat_chatgpt_power, new.summary_max_lines, new.translation_engine), (2,17,'chrome'))
        self.assertEqual(settings.Settings(chat_chatgpt_power=0).chat_chatgpt_power, 0)
        # An empty/partial old settings file must not replace release values
        # with hardcoded legacy migration fallbacks.
        settings.SETTINGS_PATH.write_text('{"chat_screenshot":false}', encoding='utf-8')
        self.assertEqual(settings.Settings.load().summary_max_lines, 17)
        self.assertFalse(settings.Settings.load().chat_screenshot)
        new.chat_chatgpt_power = 1
        new.save()
        self.document['settings']['chat_chatgpt_power'] = 0
        self.write()
        self.assertEqual(settings.Settings.load().chat_chatgpt_power, 1)

    def test_database_preferences_and_realtime_configs_override_release_defaults(self):
        self.document['web'].update(theme='dark', initial_tab='realtime', realtime_auto_scroll=False)
        self.document['livecaptions'].update(microphone=True, language='English (United States)')
        self.document['realtime_chat'].update(interval=45, power=0, summary_power=2, actions_prompt='Return actions JSON')
        self.document['research'].update(ancestor_depth=3, screenshot=True, tab_mode='branch')
        self.write()
        boot = self.client.get('/api/bootstrap').json()
        self.assertEqual(boot['settings']['web_theme'], 'dark')
        self.assertEqual(boot['ui_defaults']['initial_tab'], 'realtime')
        self.assertTrue(self.service.store.livecaptions_preferences()['microphone'])
        self.service.store.save_web_theme('light')
        self.service.store.save_livecaptions_preferences({'microphone':False,'language':''})
        self.assertEqual(self.service.store.web_theme(), 'light')
        self.assertFalse(self.service.store.livecaptions_preferences()['microphone'])
        base = f'/api/conversations/{self.cid}/realtime-chat'
        rt = self.client.get(base).json()
        self.assertEqual((rt['config']['interval'], rt['config']['power'], rt['config']['summary_power']), (45,0,2))
        self.assertFalse(rt['auto_scroll'])
        # API model defaults must not overwrite omitted release values.
        response = self.client.put(base + '/settings', headers=self.headers, json={'interval':60})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['power'], 0)
        self.document['realtime_chat'].update(interval=90, power=1)
        self.write()
        self.assertEqual(self.client.get(base).json()['config']['interval'], 60)
        self.assertEqual(self.client.get(base).json()['config']['power'], 0)
        self.assertEqual(self.client.get('/api/realtime-prompts').json()['actions'][0]['text'], 'Return actions JSON')
        store = self.service.research.store
        self.assertEqual(store.defaults()['ancestor_depth'], 3)
        store.save_defaults({'ancestor_depth':1, 'screenshot':False})
        self.assertEqual(store.defaults()['ancestor_depth'], 1)
        self.assertFalse(store.defaults()['screenshot'])

    def test_popup_first_write_keeps_other_release_defaults_and_saved_values(self):
        self.document['realtime_popup'].update(font_size=9, opacity=70)
        self.write()
        popup = self.service.realtime_popups
        self.assertEqual((popup.font_size(), popup.opacity()), (9,70))
        popup.native = Mock()
        popup.native.valid.return_value = True
        popup.windows[self.cid] = {'hwnd':123, 'owner':456}
        popup.set_opacity(self.cid, 80)
        self.assertEqual(popup.font_size(), 9)
        popup.set_font_size(self.cid, 4)
        self.assertEqual(popup.opacity(), 80)
        self.document['realtime_popup'].update(font_size=20, opacity=100)
        self.write()
        self.assertEqual((popup.font_size(), popup.opacity()), (4,80))

    def test_graph_defaults_apply_without_overwriting_saved_layout(self):
        self.document['research_graph'].update(snap=False, wheel_zoom=False, mode='hand')
        self.write()
        mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Root')
        base = f'/api/conversations/{self.cid}/research'
        node = self.post(base + '/import', {'message_id':mid}).json()
        store = self.service.research.store
        layout = store.graph_layout(self.cid, node['id'])
        self.assertFalse(layout['snap'])
        self.assertEqual(layout['mode'], 'hand')
        store.save_graph_layout(self.cid, node['id'], {'snap':True})
        self.assertTrue(store.graph_layout(self.cid, node['id'])['snap'])
        self.assertFalse(store.graph_layout(self.cid, node['id'])['wheel_zoom'])

    def test_invalid_release_values_fail_with_field_name(self):
        for section, key, value in [('settings','chat_screenshot','false'), ('realtime_popup','font_size',0),
                                    ('realtime_chat','interval',2), ('research','tab_mode','both'),
                                    ('web','theme','blue')]:
            with self.subTest(section=section, key=key):
                data = json.loads(json.dumps(self.document))
                data[section][key] = value
                with self.assertRaisesRegex(ValueError, section + r'\.' + key):
                    defaults.validate(data)
        data['extra'] = {}
        with self.assertRaises(ValueError):
            defaults.validate(data)
        path = self.bundle / 'config/defaults.json'
        path.write_text('{"schema_version":1,"schema_version":2}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate key'):
            defaults.check()
        self.write()

    def test_only_bundle_config_is_loaded_and_both_builders_include_it(self):
        # A user-data copy is not the release configuration.
        self.document['settings']['chat_max_lines'] = 19
        self.write()
        with patch.object(paths, 'DATA_DIR', self.root / 'user-data'):
            self.assertEqual(settings.Settings().chat_max_lines, 19)
        root = Path(__file__).resolve().parents[1]
        for filename in ('build_web_exe.py', 'build_exe.py'):
            spec = importlib.util.spec_from_file_location('builder', root / 'packaging' / filename)
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
            args = builder.pyinstaller_args('v1.2.3', self.root/'version.txt') if 'web' in filename else builder.pyinstaller_args(False)
            self.assertIn(str(root/'config/defaults.json') + ';config', args)
            self.assertFalse(any('settings.json' in arg for arg in args))


if __name__ == '__main__':
    unittest.main()
