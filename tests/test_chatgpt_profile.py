import unittest
from unittest.mock import patch

from caption import chatgpt
from caption.settings import Settings
import test_web_api as fixtures


class ChatGPTProfileTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def change(self, **settings):
        return self.client.patch('/api/settings', json=settings, headers=self.headers)

    def upload(self):
        return self.client.post(f'/api/conversations/{self.cid}/files', headers=self.headers,
                                files={'file': ('test.png', b'image', 'image/png')})

    def test_profile_persists_and_switching_plan_restores_upload(self):
        self.assertEqual(self.change(chatgpt_plan='invalid').status_code, 400)
        self.assertEqual(self.change(chatgpt_login_status='invalid').status_code, 400)
        with patch('web_api.server.chatgpt.require_browser_login'):
            self.assertEqual(self.change(chatgpt_plan='free', chatgpt_login_status='signed_in').status_code, 200)
        profile = self.client.get('/api/bootstrap').json()['settings']
        self.assertEqual((profile['chatgpt_plan'], profile['chatgpt_login_status']), ('free', 'signed_in'))
        self.assertEqual(self.upload().status_code, 400)
        with patch('PIL.ImageGrab.grab') as grab:
            self.assertEqual(self.post(f'/api/conversations/{self.cid}/screenshot').status_code, 400)
            grab.assert_not_called()
        self.assertEqual(self.change(chatgpt_plan='pro').status_code, 200)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(Settings.load().chatgpt_login_status, 'signed_in')

    def test_signed_out_blocks_chatgpt_endpoints_and_keeps_windows_capture(self):
        self.assertEqual(self.change(web_capture_source='chatgpt').status_code, 200)
        self.assertEqual(self.change(chatgpt_login_status='signed_out').status_code, 200)
        self.assertEqual(Settings.load().web_capture_source, 'windows')
        self.assertEqual(self.change(web_capture_source='chatgpt').status_code, 400)
        base = f'/api/conversations/{self.cid}'
        for route, payload in [('/api/chrome-tabs', {}), *[(base + '/jobs', {'kind': kind}) for kind in ('chat', 'summary', 'vocab')],
                               (base + '/realtime-chat/start', {}), (base + '/realtime-chat/messages', {'message': 'hello'}),
                               (base + '/realtime-popup', {}), (base + '/answers/123/recopy', {}), ('/api/overlay', {})]:
            with self.subTest(route=route, payload=payload):
                response = self.post(route, payload)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('Các thao tác ChatGPT đang tắt', response.json()['detail'])
        self.assertEqual(self.client.get('/api/chrome-tabs').status_code, 400)
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.post(base + '/capture').json()['capture']['source'], 'windows')
        self.post('/api/capture/pause')
        with patch('web_api.server.chatgpt.require_browser_login'):
            self.assertEqual(self.change(chatgpt_login_status='signed_in').status_code, 200)
        self.assertEqual(self.upload().status_code, 200)

    def test_login_claim_requires_browser_evidence(self):
        self.assertEqual(self.change(chatgpt_login_status='signed_out').status_code, 200)
        for error, status in ((chatgpt.ChatGPTLoginRequired(), 'signed_out'),
                              (chatgpt.ChatGPTLoginUnverified(), 'unknown'),
                              (chatgpt.ChatGPTVerificationRequired(), 'unknown')):
            with self.subTest(status=status), patch('web_api.server.chatgpt.require_browser_login', side_effect=error):
                self.assertEqual(self.change(chatgpt_login_status='signed_in').status_code, 400)
                self.assertEqual(Settings.load().chatgpt_login_status, 'signed_out')
                self.assertEqual(self.client.get('/api/chatgpt/login-status').json()['status'], status)
        with patch('web_api.server.chatgpt.require_browser_login'):
            self.assertEqual(self.client.get('/api/chatgpt/login-status').json()['status'], 'signed_in')
            self.assertEqual(self.change(chatgpt_login_status='signed_in').status_code, 200)
        self.assertEqual(Settings.load().chatgpt_login_status, 'signed_in')

    def test_signed_out_target_turns_off_chatgpt_and_unknown_keeps_current_setting(self):
        self.assertEqual(self.change(web_capture_source='chatgpt').status_code, 200)
        for error, expected in ((chatgpt.ChatGPTLoginUnverified(), 'signed_in'),
                                (chatgpt.ChatGPTLoginRequired(), 'signed_out')):
            with patch('web_api.server.chatgpt.open_new_chatgpt_tab', side_effect=error):
                self.assertEqual(self.post('/api/chrome-tabs').status_code, 400)
                self.assertEqual(Settings.load().chatgpt_login_status, expected)
        self.assertEqual(Settings.load().web_capture_source, 'windows')

    def test_sign_out_stops_chatgpt_capture_and_realtime(self):
        self.assertEqual(self.change(web_capture_source='chatgpt').status_code, 200)
        base = f'/api/conversations/{self.cid}'
        with patch('web_api.service.chatgpt_dictation.ChatGPTDictationReader', side_effect=lambda emit, *_: fixtures.FakeReader(emit)), \
             patch.object(self.service.realtime_chat, 'stop', wraps=self.service.realtime_chat.stop) as stop:
            self.assertEqual(self.post(base + '/capture').json()['capture']['source'], 'chatgpt')
            self.assertEqual(self.change(chatgpt_login_status='signed_out').status_code, 200)
            stop.assert_called()
            capture = self.client.get('/api/bootstrap').json()['capture']
            self.assertFalse(capture['active'])
            self.assertEqual(capture['source'], 'windows')

    def test_free_chat_suppresses_automatic_attachments_and_summary_uses_text(self):
        settings = Settings(chatgpt_plan='free', summary_attach_transcript=True)
        payload = dict(selected_tab='tab', message='Question', screenshot=True, attach_transcript=True, attach_history=True)
        with patch.object(self.service, 'transcript_for') as transcript, patch('web_api.screen.capture') as screenshot:
            original = self.root / 'transcript.txt'
            original.write_text('These are the saved captions.', encoding='utf-8')
            transcript.return_value.source = 'Windows Live Captions'
            transcript.return_value.started = None
            transcript.return_value.snapshot.return_value = original
            self.assertEqual(self.service.prepare_request(self.cid, 'chat', payload, settings)['attachments'], [])
            transcript.return_value.snapshot.assert_not_called()
            screenshot.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'ChatGPT Free'):
                self.service.prepare_request(self.cid, 'chat', {**payload, 'files': ['existing.png']}, settings)
            summary = self.service.prepare_request(self.cid, 'summary', payload, settings)
            self.assertEqual(summary['attachments'], [])
            self.assertIn('These are the saved captions.', summary['prompt'])

    def test_free_branch_keeps_ancestor_context_inline_and_realtime_blocks_files(self):
        self.assertEqual(self.change(chatgpt_plan='free').status_code, 200)
        self.service.busy = True
        mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'An ancestor answer')
        parent = self.service.research.store.import_answer(self.cid, mid)
        with patch('web_api.screen.capture') as screenshot:
            new, = self.service.research.children(self.cid, parent['id'], {
                'questions': ['Continue'], 'config': {'selected_tab': 'tab', 'ancestor_depth': 1, 'screenshot': True},
            })
            screenshot.assert_not_called()
        stored = self.service.research.store.get(self.cid, new['id'], private=True)['request']
        self.assertFalse(stored['attachments'])
        self.assertIn('An ancestor answer', stored['prompt'])

        realtime = self.service.realtime_chat
        realtime.store.save(self.cid, {**realtime.defaults(), 'selected_tab': 'tab'})
        path = f'/api/conversations/{self.cid}/realtime-chat'
        self.assertFalse(self.client.get(path).json()['attachments_allowed'])
        self.assertEqual(self.post(path + '/messages', {'message': 'Hi', 'files': ['existing.png']}).status_code, 400)
