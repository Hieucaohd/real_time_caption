import unittest
from unittest.mock import patch

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
        self.assertEqual(self.change(chatgpt_plan='free', chatgpt_login_status='signed_out').status_code, 200)
        profile = self.client.get('/api/bootstrap').json()['settings']
        self.assertEqual((profile['chatgpt_plan'], profile['chatgpt_login_status']), ('free', 'signed_out'))
        self.assertEqual(self.upload().status_code, 400)
        with patch('PIL.ImageGrab.grab') as grab:
            self.assertEqual(self.post(f'/api/conversations/{self.cid}/screenshot').status_code, 400)
            grab.assert_not_called()
        self.assertEqual(self.change(chatgpt_plan='pro').status_code, 200)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(Settings.load().chatgpt_login_status, 'signed_out')

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
