from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError as exc:
    raise unittest.SkipTest('Install requirements-web.txt to run web API tests.') from exc

from caption import conversations, paths, prompt_versions, settings
from caption.conversations import ConversationStore, ConversationTranscript
from caption.shared_access import BusyError
from caption.transcriber import Event
from web_api.server import create_app, browser_markdown
from web_api.service import Service


class FakeReader:
    def __init__(self, emit):
        self.emit = emit

    def start(self):
        self.emit(Event('ready', 'Reading'))

    def stop(self):
        self.emit(Event('stopped'))


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'conversations'
        self.patches = [
            patch.object(conversations, 'CONVERSATIONS_DIR', self.folder),
            patch.object(settings, 'SETTINGS_PATH', self.root / 'settings.json'),
            patch.object(paths, 'CACHE_DIR', self.root / 'cache'),
            patch.object(prompt_versions, 'CHAT_DIR', self.root / 'chat-prompts'),
            patch.object(prompt_versions, 'SUMMARY_DIR', self.root / 'summary-prompts'),
            patch('web_api.service.ConversationStore', lambda: ConversationStore(self.root / 'data.sqlite3')),
            patch('web_api.service.livecaptions.LiveCaptionsReader', FakeReader),
        ]
        for p in self.patches:
            p.start()
        settings.Settings(chat_screenshot=False).save()
        prompt_versions.save_new_version('Question: {message}', 'default', prompt_versions.CHAT_DIR)
        prompt_versions.save_new_version('Summarize {file_name}', 'default', prompt_versions.SUMMARY_DIR)
        self.service = Service()
        self.client = TestClient(create_app(self.service))
        self.client.__enter__()
        self.headers = {'X-RTC-Client': 'web'}
        self.cid = self.post('/api/conversations', {'title': 'Shared lesson'}).json()['id']

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def post(self, path, body=None):
        return self.client.post(path, json=body or {}, headers=self.headers)

    def wait_job(self, jid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.service.jobs[jid]
            if job['status'] not in ('queued', 'working') and not self.service.busy:
                return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_shared_capture_lifecycle_and_native_data(self):
        self.assertEqual(self.post(f'/api/conversations/{self.cid}/capture').status_code, 200)
        native = ConversationStore(self.service.store.db_path)
        self.assertEqual(native.get(self.cid).status, 'recording')
        other = ConversationTranscript(native.get(self.cid), native)
        with self.assertRaises(BusyError):
            other.begin_segment()
        self.service.on_caption(Event('final', 'Shared live caption.', end_of_utterance=True))
        self.assertIn('Shared live caption.', other.read_text())
        self.assertEqual(self.client.patch(f'/api/conversations/{self.cid}', json={'title':'Rename'}, headers=self.headers).status_code,409)
        self.post('/api/capture/pause')
        self.assertEqual(native.get(self.cid).status, 'paused')
        native.rename(self.cid, 'Renamed in desktop')
        native.add_message(self.cid, 'bot', 'ChatGPT', '**Shared answer**')
        detail = self.client.get(f'/api/conversations/{self.cid}').json()
        self.assertEqual(detail['conversation']['title'], 'Renamed in desktop')
        self.assertEqual(detail['messages'][0]['content'], '**Shared answer**')
        self.assertEqual(len(detail['lines']), 1)
        self.assertIn('Shared live caption.', self.client.get(f'/api/conversations/{self.cid}/transcript').text)

    def test_upload_paths_and_foreign_origin_are_rejected(self):
        response = self.client.post(f'/api/conversations/{self.cid}/files', headers=self.headers,
            files={'file': ('../../example.txt', b'context', 'text/plain')})
        self.assertEqual(response.status_code, 200)
        path = response.json()['path']
        self.assertEqual(self.client.get(f'/api/conversations/{self.cid}/files/{path}').content, b'context')
        with self.assertRaises(ValueError):
            self.service.file_path(self.cid, '../../data.sqlite3')
        self.assertEqual(self.client.post('/api/conversations',json={'title':'bad'}).status_code,403)
        self.assertEqual(self.client.post('/api/conversations',json={'title':'bad'},
            headers={**self.headers,'Origin':'https://attacker.test'}).status_code,403)

    def test_capture_releases_owner_when_translation_flush_fails(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        with patch.object(self.service.batcher, 'flush', side_effect=RuntimeError('flush failed')):
            self.post('/api/capture/pause')
        self.assertFalse(self.service.state()['capture']['active'])
        self.assertIsNone(self.service.reader)
        self.assertEqual(self.post(f'/api/conversations/{self.cid}/capture').status_code, 200)
        self.post('/api/capture/pause')

    def test_translation_stop_then_enable_resumes_and_clears_preview(self):
        self.service.worker._paused = True
        self.service.preview_snapshot = ('stale',)
        self.post('/api/translation/stop')
        self.assertIsNone(self.service.preview_snapshot)
        response = self.client.patch('/api/settings', json={'translation_enabled': True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.service.worker._paused)

    def test_settings_merge_preserves_changes_from_both_interfaces(self):
        initial = settings.Settings(); initial.save()
        native = settings.Settings.load()
        response = self.client.patch('/api/settings',json={'summary_max_lines':25},headers=self.headers)
        self.assertEqual(response.status_code,200)
        native.chat_max_lines = 40
        native.save()
        current = settings.Settings.load()
        self.assertEqual(current.summary_max_lines,25)
        self.assertEqual(current.chat_max_lines,40)
        self.assertEqual(self.client.patch('/api/settings',json={'unknown':1},headers=self.headers).status_code,422)
        self.assertEqual(self.client.patch('/api/settings',json={'translation_batch_size':0},headers=self.headers).status_code,400)

    def test_chat_and_summary_store_results_and_keep_prompt_and_attachments(self):
        self.post(f'/api/conversations/{self.cid}/capture')
        self.service.on_caption(Event('final','Original caption',end_of_utterance=True))
        self.post('/api/capture/pause')
        with patch('web_api.service.chatgpt.ask', return_value='## Saved\n\n**Answer**') as ask:
            job = self.post(f'/api/conversations/{self.cid}/jobs',
                {'kind':'chat','message':'Explain','selected_tab':'tab','attach_transcript':True}).json()
            result = self.wait_job(job['id'])
            self.assertEqual(result['status'],'done',result['error'])
            self.assertEqual(ask.call_args.args[0],'Question: Explain')
            attachment=ask.call_args.args[1][0]
            self.assertIn('Original caption',attachment.read_text(encoding='utf-8'))
            self.assertEqual(self.service.store.messages(self.cid)[-1].content,'## Saved\n\n**Answer**')
            summary = self.post(f'/api/conversations/{self.cid}/jobs',
                {'kind':'summary','selected_tab':'tab','attach_transcript':True}).json()
            result = self.wait_job(summary['id'])
            self.assertEqual(result['status'],'done',result['error'])
            self.assertEqual(self.service.store.latest_summary(self.cid).content,'## Saved\n\n**Answer**')

    def test_failed_chat_can_retry_without_duplicate_user_message(self):
        with patch('web_api.service.chatgpt.ask',side_effect=[RuntimeError('test failure'),'Recovered']):
            first=self.post(f'/api/conversations/{self.cid}/jobs',{'message':'Hello','selected_tab':'tab'}).json()
            self.assertEqual(self.wait_job(first['id'])['status'],'error')
            second=self.post('/api/jobs/'+first['id']+'/retry').json()
            self.assertEqual(self.wait_job(second['id'])['status'],'done')
        messages=self.service.store.messages(self.cid)
        self.assertEqual([m.role for m in messages],['user','bot'])

    def test_prompt_versions_share_native_directory_and_reject_traversal(self):
        result=self.post('/api/prompts/chat',{'name':'Web version','text':'New {message}'}).json()
        self.assertIn(result['name'],prompt_versions.list_versions(prompt_versions.CHAT_DIR))
        self.assertEqual(self.post('/api/prompts/chat',{'name':'../../private','text':'bad','overwrite':True}).status_code,400)

    def test_export_markdown_and_word(self):
        mid=self.service.store.add_message(self.cid,'bot','ChatGPT','## Answer\n\n**Bold**')
        base=f'/api/conversations/{self.cid}/answers/{mid}/export'
        self.assertEqual(self.client.get(base+'?format=md').text,'## Answer\n\n**Bold**')
        self.assertTrue(self.client.get(base+'?format=docx').content.startswith(b'PK'))

    def test_vocabulary_export_uses_cards_instead_of_raw_json(self):
        mid=self.service.store.add_message(self.cid, 'bot', 'Vocabulary', '[{"word":"hello","translation":"xin chao"}]', kind='vocab')
        base=f'/api/conversations/{self.cid}/answers/{mid}/export'
        self.assertIn('## hello', self.client.get(base+'?format=md').text)
        self.assertTrue(self.client.get(base+'?format=docx').content.startswith(b'PK'))
        self.assertTrue(self.client.get(base+'?format=pdf').content.startswith(b'%PDF'))


class WebMathTests(unittest.TestCase):
    def test_native_copied_math_and_code_are_preserved(self):
        text=r'Inline \\(x\_1\\), display \\[x\_2\\], code `\\(x\_3\\)`.'
        result=browser_markdown(text)
        self.assertIn('$x_1$',result)
        self.assertIn('$$x_2$$',result)
        self.assertIn(r'`\\(x\_3\\)`',result)


if __name__ == '__main__':
    unittest.main()
