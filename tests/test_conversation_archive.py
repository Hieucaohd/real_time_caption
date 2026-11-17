from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import test_web_api as fixtures
from caption import conversations
from caption.conversations import ConversationStore, ConversationTranscript
from caption.research_store import ResearchStore
from caption.translation_store import TranslationStore
from caption.translation import Config
from caption.shared_access import capture_lock
from web_api.conversation_archive import import_conversation, export_conversation


class ConversationArchiveTests(unittest.TestCase):
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.service.busy = True
        self.store = self.service.store
        self.research = self.service.research.store
        self.conv = self.store.get(self.cid)
        (self.conv.folder / 'transcript.txt').write_bytes(b'[00:01] Hello\n')
        (self.conv.folder / 'transcript-0002.txt').write_bytes(b'[00:02] Second part\n')
        (self.conv.folder / 'summaries.md').write_text('Saved summary', encoding='utf-8')
        (self.conv.folder / 'attachments').mkdir()
        self.photo = self.conv.folder / 'attachments' / 'ảnh.png'
        self.photo.write_bytes(b'image bytes')
        external = self.root / 'outside.txt'; external.write_bytes(b'external attachment')
        self.store.add_message(self.cid, 'user', 'You', 'Question', attachment=self.photo, files=(self.photo, external))
        self.mid = self.store.add_message(self.cid, 'bot', 'ChatGPT', 'Alpha concept. $x^2$',
            response_conversation_url='https://chatgpt.com/g/project/c/source', response_position=3, response_tab_title='Source tab')
        self.store.add_message(self.cid, 'bot', 'Summary', '# Summary', kind='summary')
        self.store.add_message(self.cid, 'bot', 'Words', '[{"word":"alpha"}]', kind='vocab')
        self.node = self.research.import_answer(self.cid, self.mid)
        mark = dict(start=0, end=13, text='Alpha concept')
        self.child = self.service.research.children(self.cid, self.node['id'], dict(questions=['Explain alpha'],
            excerpt='Alpha concept', parent_reading_mark=mark, files=['attachments/ảnh.png'], config={'selected_tab':'old-chrome-tab'}))[0]
        self.research.update(self.cid, self.child['id'], status='done', answer='Child answer', understood=1)
        self.research.update_response_source(self.cid, {'response_conversation_url':'https://chatgpt.com/c/child', 'response_position':1}, nid=self.child['id'])
        self.grand = self.service.research.children(self.cid, self.child['id'], dict(questions=['Deeper'],
            config={'selected_tab':'old-chrome-tab', 'ancestor_depth':-1}))[0]
        self.research.set_hidden(self.cid, self.child['id'], True)
        self.research.save_graph_layout(self.cid, self.node['id'], {'positions': {
            self.node['id']: {'x':10, 'y':20}, self.child['id']: {'x':400, 'y':30}, self.grand['id']: {'x':800, 'y':40}},
            'collapsed':[self.child['id']], 'camera':{'x':12, 'y':23, 'zoom':.75}})
        self.service.translations.sync_transcript(ConversationTranscript(self.conv, self.store))
        self.lines = self.service.translations.lines(self.cid)
        self.service.translations.save_realtime(self.cid, 'live-passage', Config(engine='chrome'), 'Hello', 'Realtime hello')
        with self.store._connect() as db:
            for line, status in zip(self.lines, ('done', 'working')):
                db.execute('''INSERT INTO line_translations(line_id,engine,model,source_lang,target_lang,fingerprint,source_text,
                    translated_text,status,created_at,owner_pid) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (line['id'],'google','model','en','vi','hash',line['source_text'],'Xin chào',status,'2026-01-01',123))

    def exported(self):
        response = self.client.get(f'/api/conversations/{self.cid}/export')
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        self.assertIn('application/zip', response.headers['content-type'])
        self.assertIn('.rtc-conversation.zip', response.headers['content-disposition'])
        return response.content

    def upload(self, content):
        return self.client.post('/api/conversations/import', headers=self.headers,
            files={'file':('conversation.zip', content, 'application/zip')})

    def mutate(self, content, change=None, extra=None):
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        if change:
            data = json.loads(entries['manifest.json']); change(data)
            entries['manifest.json'] = json.dumps(data, ensure_ascii=False).encode()
        if extra:
            entries.update(extra)
        result = BytesIO()
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items(): archive.writestr(name, content)
        return result.getvalue()

    def test_roundtrip_to_second_device_preserves_all_conversation_data(self):
        other = self.store.create('Other', 'Unrelated private conversation')
        (other.folder / 'private.txt').write_text('Do not export', encoding='utf-8')
        package = self.exported()
        with zipfile.ZipFile(BytesIO(package)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertNotIn('private.txt', '\n'.join(archive.namelist()))
            self.assertNotIn('Do not export', json.dumps(manifest))
            self.assertNotIn('research_preferences', manifest)
            for node in manifest['research_nodes']:
                self.assertNotIn(str(self.root), node['request'])
        original_nodes = {node['id'] for node in self.research.list(self.cid)}
        with patch.object(conversations, 'CONVERSATIONS_DIR', self.root / 'second-device'):
            target = ConversationStore(self.root / 'second.sqlite3')
            research = ResearchStore(target); translations = TranslationStore(target.db_path)
            service = SimpleNamespace(store=target, lock=threading.RLock())
            imported = import_conversation(service, BytesIO(package))
            self.assertNotEqual(imported.id, self.cid)
            self.assertEqual(imported.title, self.conv.title)
            self.assertEqual(imported.status, 'paused')
            self.assertEqual(imported.created_at, self.conv.created_at)
            for name in ('transcript.txt','transcript-0002.txt','summaries.md','attachments/ảnh.png'):
                self.assertEqual((imported.folder / name).read_bytes(), (self.folder / self.conv.folder_name / name).read_bytes())
            messages = target.messages(imported.id)
            self.assertEqual([m.content for m in messages], [m.content for m in self.store.messages(self.cid)])
            self.assertTrue(all(not Path(path).is_absolute() for path in messages[0].files))
            self.assertEqual((imported.folder / messages[0].files[1]).read_bytes(), b'external attachment')
            self.assertEqual(messages[1].response_position, 3)
            nodes = research.list(imported.id)
            self.assertTrue(original_nodes.isdisjoint({n['id'] for n in nodes}))
            root = next(n for n in nodes if not n['parent_id'])
            child = next(n for n in nodes if n['parent_id'] == root['id'])
            grand = next(n for n in nodes if n['parent_id'] == child['id'])
            self.assertEqual(root['source_message_id'], messages[1].id)
            self.assertEqual(root['reading_mark']['text'], 'Alpha concept')
            self.assertEqual(child['excerpt_mark']['text'], 'Alpha concept')
            self.assertTrue(child['hidden']); self.assertTrue(child['understood'])
            self.assertEqual(research.branch_marks(imported.id, root['id'])[0]['id'], child['id'])
            self.assertEqual(grand['status'], 'interrupted')
            self.assertEqual(child['config']['selected_tab'], '')
            self.assertEqual(child['response_conversation_url'], 'https://chatgpt.com/c/child')
            layout = research.graph_layout(imported.id, root['id'])
            self.assertEqual(layout['positions'][child['id']], {'x':400, 'y':30})
            self.assertEqual(layout['collapsed'], [child['id']])
            self.assertEqual(layout['camera'], {'x':12, 'y':23, 'zoom':.75})
            restored = research.restore_attachments(imported.id, child['id'], self.root / 'retry-files')
            self.assertEqual(restored[0].read_bytes(), b'image bytes')
            request = research.get(imported.id, grand['id'], private=True)['request']
            self.assertEqual(request['ancestor_ids'], [root['id'], child['id']])
            self.assertEqual(request['prompt'], self.research.get(self.cid, self.grand['id'], private=True)['request']['prompt'])
            imported_lines = translations.lines(imported.id)
            self.assertEqual([line['source_text'] for line in imported_lines], [line['source_text'] for line in self.lines])
            self.assertEqual(translations.history(imported_lines[0]['id'])[0]['translated_text'], 'Xin chào')
            self.assertEqual(translations.history(imported_lines[1]['id'])[0]['status'], 'error')
            self.assertEqual(translations.reading(imported.id, Config(engine='chrome'))['realtime'][0]['text'], 'Realtime hello')
            translations.sync_transcript(ConversationTranscript(imported, target))
            self.assertEqual(len(translations.lines(imported.id)), 2)
            sidecar = json.loads((imported.folder / 'conversation.json').read_text(encoding='utf-8'))
            self.assertEqual(sidecar['id'], imported.id)
            # A second export/import is portable too, including SQLite-only retry files.
            destination = self.root / 'second-export.zip'
            export_conversation(service, imported.id, destination)
            duplicate = import_conversation(service, destination)
            self.assertNotEqual(duplicate.id, imported.id)
            self.assertEqual(len(target.list()), 2)
            self.assertEqual(len(research.list(duplicate.id)), 3)

    def test_import_creates_new_ids_and_preserves_existing_conversations(self):
        content = self.exported()
        first, second = self.upload(content), self.upload(content)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len({self.cid, first.json()['id'], second.json()['id']}), 3)
        self.assertEqual(len(self.store.messages(self.cid)), 4)
        self.assertEqual(self.research.get(self.cid, self.grand['id'])['status'], 'queued')

    def test_legacy_archive_without_realtime_history_remains_importable(self):
        content = self.mutate(self.exported(), lambda data: data.pop('realtime_translations'))
        result = self.upload(content)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.service.translations.reading(result.json()['id'], Config(engine='chrome'))['realtime'], [])

    def test_invalid_archives_leave_no_rows_or_files(self):
        content = self.exported()
        cases = [b'not a zip', self.mutate(content, lambda d: d.update(version=999)),
            self.mutate(content, extra={'../escape.txt':b'bad'}),
            self.mutate(content, extra={'files/attachments/ảnh.png':b'corrupted'}),
            self.mutate(content, lambda d: d['chat_messages'][0].update(attachment='../../outside.txt')),
            self.mutate(content, lambda d: d['research_nodes'][1].update(parent_id='missing-parent')),
            self.mutate(content, lambda d: d['chat_attachments'][0].update(message_id=98765)),
            self.mutate(content, lambda d: d['research_node_assets'][0].update(filename='../outside.txt')),
            self.mutate(content, lambda d: d['translation_lines'][0].update(conversation_id='other'))]
        before = {p.name for p in self.folder.iterdir()}
        for malformed in cases:
            with self.subTest(size=len(malformed)):
                result = self.upload(malformed)
                self.assertEqual(result.status_code, 400, result.text)
                self.assertEqual(len(self.store.list()), 1)
                self.assertEqual({p.name for p in self.folder.iterdir()}, before)
        with patch('web_api.conversation_archive.MAX_EXPANDED', 10):
            self.assertEqual(self.upload(content).status_code, 400)

    def test_late_database_failure_rolls_back_imported_folder_too(self):
        content = self.exported()
        original = self.store._connect
        @contextmanager
        def failing_commit():
            with original() as db:
                yield db
                raise sqlite3.IntegrityError('simulated commit failure')
        before = {p.name for p in self.folder.iterdir()}
        with patch.object(self.store, '_connect', failing_commit):
            with self.assertRaises(ValueError): import_conversation(self.service, BytesIO(content))
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual({p.name for p in self.folder.iterdir()}, before)

    def test_access_capture_lock_and_missing_conversation(self):
        self.assertEqual(self.client.post('/api/conversations/import', files={'file':('x.zip', b'bad')}).status_code, 403)
        self.assertEqual(self.client.get('/api/conversations/missing/export').status_code, 404)
        with capture_lock(self.store.db_path):
            self.assertEqual(self.client.get(f'/api/conversations/{self.cid}/export').status_code, 409)


if __name__ == '__main__': unittest.main()
