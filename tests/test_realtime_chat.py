import json
import threading
import time
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption.chatgpt import ChromeChatGPTTab, ChatGPTCancelled
from web_api.realtime_chat import parse_actions
from web_api.realtime_chat_store import transcript_delta, RealtimeChatStore
from web_api.conversation_archive import export_conversation, import_conversation


def tab(key):
    return ChromeChatGPTTab(key=key, title=key, label=key, url='https://chatgpt.com/', focused=False)


class DeltaTests(unittest.TestCase):
    def test_delta_caps_backlog_without_repeating_or_dropping_text(self):
        lines = [dict(origin=str(i), source_text=text) for i, text in enumerate(['Old context', 'abcdefghi', 'Later'])]
        first, cursor = transcript_delta(lines, {}, initial_limit=2, max_chars=5)
        second, cursor = transcript_delta(lines, cursor, max_chars=7)
        third, cursor = transcript_delta(lines, cursor, max_chars=7)
        self.assertEqual((first, second, third), ('abcde', 'fghi\nLa', 'ter'))
        self.assertEqual(transcript_delta(lines, cursor)[0], '')
        lines.append(dict(origin='3', source_text='Brand new'))
        self.assertEqual(transcript_delta(lines, cursor)[0], 'Brand new')
        with self.assertRaisesRegex(ValueError, 'mốc transcript'):
            transcript_delta(lines, {'origin': 'missing'})

    def test_json_is_validated_not_treated_as_code(self):
        actions = parse_actions('```json\n{"actions":[{"label":"NVIDIA?","prompt":"Explain NVIDIA"}]}\n```')
        self.assertEqual(actions[0]['prompt'], 'Explain NVIDIA')
        self.assertTrue(actions[0]['id'])
        for value in ('not JSON', '[]', '{"actions":[{"label":4,"prompt":"a"}]}', '{"actions":[null]}'):
            with self.assertRaises(ValueError):
                parse_actions(value)


class RealtimeChatTests(unittest.TestCase):
    post = fixtures.WebApiTests.post

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.rt = self.service.realtime_chat
        self.base = f'/api/conversations/{self.cid}/realtime-chat'
        self.config = {**self.rt.defaults(), 'selected_tab':'actions-tab', 'summary_tab':'summary-tab',
                       'interval':5, 'screenshot':False, 'attach_transcript':True, 'max_lines':0}
        self.rt.store.save(self.cid, self.config)
        self.calls = []
        self.powers = []
        self.calls_lock = threading.Lock()
        self.chrome_patches = [
            patch('web_api.realtime_chat.chatgpt.list_chrome_chatgpt_tabs', return_value=[tab('actions-tab'), tab('summary-tab')]),
            patch('web_api.realtime_chat.chatgpt.ask', side_effect=self.ask),
        ]
        for item in self.chrome_patches:
            item.start()
        self.add_line('First new transcript')

    def tearDown(self):
        self.rt.close()
        for item in reversed(self.chrome_patches):
            item.stop()
        fixtures.WebApiTests.tearDown(self)

    def add_line(self, text):
        path = self.service.conversation(self.cid).folder / 'transcript.txt'
        with path.open('a', encoding='utf-8') as output:
            output.write('[00:01] ' + text + '\n')

    def ask(self, prompt, files, on_status, **kwargs):
        with self.calls_lock:
            self.calls.append((kwargs['selected_tab'], prompt, files, kwargs['new_chat']))
            self.powers.append((kwargs['selected_tab'], kwargs['power']))
        on_status('Fake response')
        kwargs['on_response_source']({'response_conversation_url':'https://chatgpt.com/c/test', 'response_position':2})
        if kwargs['selected_tab'] == 'summary-tab':
            return 'Summary of new content'
        if '"actions"' in prompt:
            return '{"actions":[{"label":"Explain","prompt":"Explain the concept"}]}'
        return 'Answer to the question'

    def wait_for(self, condition):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.02)
        self.fail('Realtime worker did not reach expected state: ' + str(self.rt.state(self.cid)['lanes']))

    def start(self):
        result = self.post(self.base + '/start', self.config)
        self.assertEqual(result.status_code, 200, result.text)

    def done(self, kind):
        return [e for e in self.rt.store.history(self.cid) if e['kind'] == kind and e['status'] == 'done']

    def due(self):
        with self.rt.lock:
            for lane in self.rt.busy:
                self.rt.runtime[self.cid, lane]['due'] = 0

    def test_two_lanes_run_concurrently_and_delta_is_sent_only_once(self):
        gate = threading.Barrier(2)
        normal = self.ask
        def parallel(*args, **kwargs):
            gate.wait(timeout=3)
            return normal(*args, **kwargs)
        with patch('web_api.realtime_chat.chatgpt.ask', side_effect=parallel):
            self.start()
            self.wait_for(lambda: self.done('actions') and self.done('summary'))
            self.assertEqual(len(self.calls), 2)
            self.add_line('Second delta only')
            self.due()
            self.wait_for(lambda: len(self.done('actions')) == 2 and len(self.done('summary')) == 2)
        for _, prompt, _, _ in self.calls[2:]:
            self.assertIn('Second delta only', prompt)
            self.assertNotIn('First new transcript', prompt)
        self.due()
        time.sleep(.4)
        self.assertEqual(len(self.calls), 4)
        state = self.client.get(self.base).json()
        self.assertEqual(state['actions'][0]['label'], 'Explain')
        self.assertEqual(self.done('summary')[0]['response_source']['response_position'], 2)

    def test_action_and_manual_questions_use_same_tab_and_prompt_and_keep_context(self):
        self.start()
        self.wait_for(lambda: self.done('actions') and self.done('summary'))
        aid = self.rt.state(self.cid)['actions'][0]['id']
        self.assertEqual(self.post(self.base + '/messages', {'action_id': aid}).status_code, 200)
        self.wait_for(lambda: self.done('chat'))
        self.assertIn('Explain the concept', self.calls[-1][1])
        self.assertEqual(self.calls[-1][0], 'actions-tab')
        self.assertFalse(self.calls[-1][3])
        self.post(self.base + '/messages', {'message': 'My own question'})
        self.wait_for(lambda: len(self.done('chat')) == 2)
        self.assertIn('My own question', self.calls[-1][1])
        self.assertNotIn('First new transcript', self.calls[-1][1])
        self.assertEqual(self.post(self.base + '/messages', {'action_id':'stale-id'}).status_code, 400)

    def test_manual_priority_and_stop_cancels_without_sending_queued_requests(self):
        entered, release = threading.Event(), threading.Event()
        normal = self.ask
        def slow(prompt, *args, **kwargs):
            if kwargs['selected_tab'] == 'actions-tab':
                entered.set()
                while not release.wait(.01):
                    if kwargs['cancel_event'].is_set():
                        raise ChatGPTCancelled('Stopped')
            return normal(prompt, *args, **kwargs)
        with patch('web_api.realtime_chat.chatgpt.ask', side_effect=slow):
            self.start()
            self.assertTrue(entered.wait(3))
            self.post(self.base + '/messages', {'message':'Priority question'})
            self.due()
            release.set()
            self.wait_for(lambda: self.done('chat'))
            actions_calls = [call for call in self.calls if call[0] == 'actions-tab']
            self.assertIn('Priority question', actions_calls[1][1])
            release.clear(); entered.clear()
            self.post(self.base + '/messages', {'message':'Cancel active'})
            self.assertTrue(entered.wait(3))
            self.post(self.base + '/messages', {'message':'Cancel queued'})
            self.post(self.base + '/stop')
            self.wait_for(lambda: not any(self.rt.busy.values()))
        events = self.rt.store.history(self.cid)
        self.assertEqual([e['status'] for e in events if e['question'].startswith('Cancel')], ['cancelled', 'cancelled'])
        self.assertFalse(self.rt.state(self.cid)['running'])

    def test_bad_json_pauses_lane_without_resending_received_delta(self):
        normal = self.ask
        with patch('web_api.realtime_chat.chatgpt.ask', side_effect=lambda *a, **kw: 'bad JSON' if kw['selected_tab']=='actions-tab' else normal(*a, **kw)):
            self.start()
            self.wait_for(lambda: self.rt.state(self.cid)['lanes']['actions'].get('status') == 'error')
            _, cursors = self.rt.store.settings(self.cid, self.config)
            self.assertTrue(cursors['actions'])
            self.post(self.base + '/stop')
            self.wait_for(lambda: not any(self.rt.busy.values()))
        self.calls.clear()
        self.start()
        time.sleep(.4)
        self.assertFalse([c for c in self.calls if c[0] == 'actions-tab'])

    def test_new_tabs_are_created_once_and_settings_and_cursor_survive_restart(self):
        self.config.update(selected_tab='', summary_tab='')
        self.rt.store.save(self.cid, self.config)
        with patch('web_api.realtime_chat.chatgpt.open_new_chatgpt_tab', side_effect=[tab('actions-tab'), tab('summary-tab')]) as opener:
            self.start()
            self.wait_for(lambda: self.done('actions') and self.done('summary'))
            self.assertEqual(opener.call_count, 2)
            self.post(self.base + '/stop')
            self.wait_for(lambda: not any(self.rt.busy.values()))
        saved, cursors = RealtimeChatStore(self.service.store).settings(self.cid, {})
        self.assertEqual(saved['selected_tab'], 'actions-tab')
        self.assertTrue(cursors['summary'])
        self.config = saved
        self.calls.clear()
        self.start()
        time.sleep(.4)
        self.assertEqual(self.calls, [])

    def test_settings_validation_and_replacement_of_missing_tabs(self):
        self.assertEqual(self.client.post(self.base+'/start', json=self.config).status_code, 403)
        self.assertEqual(self.post(self.base+'/start', {**self.config,'interval':0}).status_code, 422)
        self.rt.store.save(self.cid, {**self.config, 'selected_tab':'missing', 'summary_tab':'missing'})
        with patch('web_api.realtime_chat.chatgpt.open_new_chatgpt_tab', side_effect=[tab('actions-tab'), tab('summary-tab')]) as opener:
            self.start()
            self.assertEqual(opener.call_count, 2)
        result = self.client.put(self.base+'/settings', json=self.config, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.post(self.base+'/messages', {'message':'Bad path','files':['../../secrets']}).status_code, 400)

    def test_duplicate_stored_tab_is_replaced_for_summary_only(self):
        self.rt.store.save(self.cid, {**self.config, 'summary_tab':'actions-tab'})
        with patch('web_api.realtime_chat.chatgpt.open_new_chatgpt_tab', return_value=tab('summary-tab')) as opener:
            self.start()
            self.assertEqual(opener.call_count, 1)
        self.wait_for(lambda: self.done('actions') and self.done('summary'))

    def test_live_power_save_updates_queued_and_future_turns_without_resetting_context(self):
        self.config['power'] = 0
        entered, release = threading.Event(), threading.Event()
        normal = self.ask
        def blocked(*args, **kwargs):
            if kwargs['selected_tab']=='actions-tab' and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(4))
            return normal(*args, **kwargs)
        with patch('web_api.realtime_chat.chatgpt.ask', side_effect=blocked):
            self.start()
            self.assertTrue(entered.wait(3))
            self.post(self.base+'/messages', {'message':'Queued question'})
            response = self.client.put(self.base+'/settings', headers=self.headers, json={**self.config, 'power':2,
                'new_chat':True, 'attach_transcript':False, 'attach_history':True, 'max_lines':1, 'selected_tab':'untrusted-tab'})
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual(saved['selected_tab'], 'actions-tab')
            self.assertTrue(saved['attach_transcript'])
            self.assertFalse(saved['new_chat'])
            self.assertFalse(saved['attach_history'])
            self.assertEqual(saved['max_lines'], 0)
            release.set()
            self.wait_for(lambda: self.done('chat') and self.done('summary'))
        self.assertEqual([power for key, power in self.powers if key=='actions-tab'], [0,2])
        self.add_line('Added after changing Power')
        self.due()
        self.wait_for(lambda: len(self.done('actions'))==2 and len(self.done('summary'))==2)
        self.assertTrue(all(power==2 for _, power in self.powers[-2:]))
        for _, prompt, _, fresh in self.calls[-2:]:
            self.assertNotIn('First new transcript', prompt)
            self.assertIn('Added after changing Power', prompt)
            self.assertFalse(fresh)

    def test_history_pagination_keeps_older_chat_and_latest_actions_accessible(self):
        self.rt.store.save(self.cid, self.config)
        first = self.rt.store.create(self.cid, 'chat', 'Old question')
        self.rt.store.update(first, 'done', 'Old answer')
        action = self.rt.store.create(self.cid, 'actions')
        self.rt.store.update(action, 'done', json.dumps(parse_actions('{"actions":[{"label":"Old action","prompt":"Old prompt"}]}')))
        with self.service.store._connect() as db:
            db.executemany("INSERT INTO realtime_chat_events(conversation_id,kind,status,answer,created_at) VALUES(?, 'summary', 'done', 'Summary', '2026-01-01')",
                           [(self.cid,)] * 201)
        state = self.client.get(self.base).json()
        self.assertEqual(len(state['events']), 200)
        self.assertEqual(state['actions'][0]['label'], 'Old action')
        earlier = self.client.get(self.base+'/history', params={'before':state['events'][0]['id']}).json()
        self.assertEqual(earlier[0]['answer'], 'Old answer')
        self.assertEqual(len(earlier), 3)

    def test_partial_tab_preparation_preserves_new_tab_and_resets_its_cursor(self):
        self.rt.store.save(self.cid, {**self.config,'selected_tab':'','summary_tab':''})
        with self.service.store._connect() as db:
            db.execute("UPDATE realtime_chat_settings SET actions_cursor=? WHERE conversation_id=?",
                       (json.dumps({'origin':'old-tab-origin','offset':50}), self.cid))
        with patch('web_api.realtime_chat.chatgpt.open_new_chatgpt_tab', side_effect=[tab('replacement'), ValueError('Second tab unavailable')]):
            response = self.post(self.base+'/start', {**self.config,'selected_tab':'','summary_tab':''})
        self.assertEqual(response.status_code, 400)
        saved, cursors = self.rt.store.settings(self.cid, {})
        self.assertEqual(saved['selected_tab'], 'replacement')
        self.assertEqual(cursors['actions'], {})
        self.assertFalse(self.rt.state(self.cid)['running'])

    def test_export_import_preserves_history_attachments_and_resets_device_tabs(self):
        self.start()
        self.wait_for(lambda: self.done('actions') and self.done('summary'))
        uploaded = self.client.post(f'/api/conversations/{self.cid}/files', headers=self.headers,
            files={'file':('test.png', b'fake image', 'image/png')}).json()
        self.post(self.base+'/messages', {'message':'Question with image', 'files':[uploaded['path']]})
        self.wait_for(lambda: self.done('chat'))
        self.post(self.base+'/stop')
        self.wait_for(lambda: not any(self.rt.busy.values()))
        bundle = self.root / 'realtime.zip'
        export_conversation(self.service, self.cid, bundle)
        imported = import_conversation(self.service, bundle)
        state = self.rt.state(imported.id)
        self.assertFalse(state['running'])
        self.assertEqual(state['config']['selected_tab'], '')
        self.assertEqual(state['config']['summary_tab'], '')
        chat = next(e for e in state['events'] if e['kind']=='chat')
        self.assertEqual(chat['answer'], 'Answer to the question')
        self.assertEqual(self.service.file_path(imported.id, chat['files'][0]).read_bytes(), b'fake image')
        self.assertEqual(state['actions'][0]['label'], 'Explain')
