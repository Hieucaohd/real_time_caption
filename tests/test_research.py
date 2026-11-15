import json
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption.research_store import ResearchStore
from caption.settings import Settings
from caption.shared_access import BusyError


class ResearchTests(unittest.TestCase):
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.store = self.service.research.store
        self.service.store.add_message(self.cid, 'user', 'You', 'Original question')
        self.mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Root explains alpha, beta, gamma and delta.')
        self.base = f'/api/conversations/{self.cid}/research'
        self.root_node = self.post(self.base + '/import', {'message_id': self.mid}).json()

    def child(self, parent=None, **kwargs):
        payload = {'questions': ['Explain alpha'], 'config': {'selected_tab': 'research-tab', 'power': 2}, **kwargs}
        response = self.post(self.base + '/' + (parent or self.root_node['id']) + '/children', payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_nodes(self, nodes, status='done'):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            results = [self.store.get(self.cid, n['id']) for n in nodes]
            if all(n['status'] == status for n in results):
                return results
            time.sleep(.02)
        self.fail(str([(n['status'], n['detail']) for n in results]))

    def test_import_is_idempotent_snapshot_and_scoped_to_conversation(self):
        again = self.post(self.base + '/import', {'message_id': self.mid}).json()
        self.assertEqual(again['id'], self.root_node['id'])
        self.assertEqual(again['question'], 'Original question')
        self.service.store.update_message_content(self.cid, self.mid, 'Edited later')
        self.assertIn('Root explains', self.store.get(self.cid, again['id'])['answer'])
        other = self.post('/api/conversations', {'title':'Other'}).json()['id']
        self.assertEqual(self.post(f'/api/conversations/{other}/research/import', {'message_id':self.mid}).status_code, 400)
        self.assertEqual(self.client.get(f'/api/conversations/{other}/research/{again["id"]}').status_code, 404)
        self.assertNotIn('request', again)

    def test_four_siblings_queue_while_first_runs_and_keep_context_independent(self):
        started, release = threading.Event(), threading.Event()
        prompts, concurrent = [], []
        def answer(prompt, attachments, on_status, **kwargs):
            concurrent.append(1)
            self.assertEqual(len(concurrent), 1)
            self.assertTrue(kwargs['new_chat'])
            self.assertEqual(kwargs['selected_tab'], 'research-tab')
            self.assertEqual(kwargs['power'], 2)
            prompts.append(prompt)
            kwargs['on_partial']('Working answer')
            started.set()
            release.wait(4)
            concurrent.pop()
            return 'Answer to ' + prompt.split('Current question:\n')[-1]
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            first = self.child(questions=['Branch question A'])
            self.assertTrue(started.wait(3))
            detail = self.client.get(self.base + '/' + first[0]['id']).json()
            self.assertEqual(detail['partial'], 'Working answer')
            self.assertEqual(self.store.get(self.cid, first[0]['id'])['partial'], 'Working answer')
            rest = self.child(questions=['Branch question B', 'Branch question C', 'Branch question D'])
            self.assertEqual([n['status'] for n in rest], ['queued'] * 3)
            release.set()
            self.wait_nodes(first + rest)
        self.assertEqual(len(prompts), 4)
        for index, prompt in enumerate(prompts):
            self.assertIn('Original question', prompt)
            for sibling in 'ABCD':
                self.assertEqual('Branch question '+sibling in prompt, sibling == 'ABCD'[index])
        self.assertEqual(len(self.service.store.messages(self.cid)), 2)

    def test_grandchild_contains_only_ancestor_answers(self):
        with patch('web_api.research.chatgpt.ask', side_effect=['Alpha answer unique', 'Sibling answer hidden', 'Deeper answer'] ) as ask:
            nodes = self.child(questions=['Alpha detail', 'Beta detail'])
            self.wait_nodes(nodes)
            child = self.child(parent=nodes[0]['id'], questions=['Deeper alpha'], excerpt='Alpha answer unique')
            self.wait_nodes(child)
            prompt = ask.call_args.args[0]
            self.assertIn('Alpha answer unique', prompt)
            self.assertIn('Original question', prompt)
            self.assertNotIn('Sibling answer hidden', prompt)
            self.assertNotIn('Beta detail', prompt)
            self.assertEqual(self.store.get(self.cid, child[0]['id'])['root_id'], self.root_node['id'])

    def test_queue_snapshots_files_settings_and_survives_rename(self):
        self.service.busy = True
        upload = self.client.post(f'/api/conversations/{self.cid}/files', headers=self.headers, files={'file':('notes.txt',b'Frozen notes')}).json()
        nodes = self.child(files=[upload['path']])
        config = Settings.load(); config.research_power = 0; config.save()
        self.service.file_path(self.cid, upload['path']).write_text('Changed after queue')
        self.service.store.rename(self.cid, 'Renamed before send')
        with patch('web_api.research.chatgpt.ask', return_value='Saved') as ask:
            self.service.busy = False
            self.wait_nodes(nodes)
            self.assertEqual(ask.call_args.kwargs['power'], 2)
            self.assertEqual(ask.call_args.args[1][0].read_text(), 'Frozen notes')

    def test_cancel_queued_does_not_send_and_retry_reuses_node(self):
        self.service.busy = True
        node = self.child()[0]
        self.post(self.base+'/'+node['id']+'/cancel')
        with patch('web_api.research.chatgpt.ask', return_value='Retry answer') as ask:
            self.service.busy = False
            self.wait_nodes([node], 'cancelled')
            response=self.post(self.base+'/'+node['id']+'/retry',{'selected_tab':'replacement-tab'})
            self.assertEqual(response.status_code,200)
            self.wait_nodes([node])
            self.assertEqual(ask.call_count,1)
            self.assertEqual(ask.call_args.kwargs['selected_tab'],'replacement-tab')
        self.assertEqual(len(self.store.list(self.cid)),2)

    def test_active_cancellation_and_provider_error_are_not_auto_retried(self):
        started=threading.Event()
        def answer(*args,**kwargs):
            started.set()
            kwargs['cancel_event'].wait(4)
            raise RuntimeError('Stopped provider')
        with patch('web_api.research.chatgpt.ask',side_effect=answer) as ask:
            node=self.child()[0]
            self.assertTrue(started.wait(3))
            self.post(self.base+'/'+node['id']+'/cancel')
            self.wait_nodes([node],'cancelled')
            self.assertEqual(ask.call_count,1)
        with patch('web_api.research.chatgpt.ask',side_effect=RuntimeError('Provider failed')) as ask:
            node=self.child()[0]
            self.wait_nodes([node],'error')
            self.assertEqual(ask.call_count,1)
            self.assertIsNone(self.store.next_queued())

    def test_native_chrome_lock_keeps_branch_queued_until_available(self):
        with patch('web_api.research.chatgpt.ask',side_effect=[BusyError('Native is sending'),'Answer']) as ask:
            node=self.child()[0]
            self.wait_nodes([node])
            self.assertEqual(ask.call_count,2)

    def test_restart_marks_abandoned_work_for_manual_retry(self):
        self.service.busy=True
        nodes=self.child(questions=['Queued', 'Interrupted'])
        self.store.update(self.cid,nodes[1]['id'],status='working')
        recovered=ResearchStore(self.service.store)
        self.assertEqual(recovered.get(self.cid,nodes[1]['id'])['status'],'interrupted')
        self.assertEqual(recovered.get(self.cid,nodes[0]['id'])['status'],'queued')
        self.assertTrue(recovered.get(self.cid,nodes[0]['id'],private=True)['request']['prompt'])

    def test_noncompleted_parent_and_invalid_questions_are_rejected(self):
        self.service.busy=True
        node=self.child()[0]
        body={'questions':['Nested'],'config':{'selected_tab':'tab'}}
        self.assertEqual(self.post(self.base+'/'+node['id']+'/children',body).status_code,400)
        for questions in ([],[' '],['x']*21):
            self.assertEqual(self.post(self.base+'/'+self.root_node['id']+'/children',{'questions':questions}).status_code,422)
        self.assertEqual(self.client.patch(self.base+'/'+node['id'],json={'understood':True},headers=self.headers).status_code,400)
        response=self.client.patch(self.base+'/'+self.root_node['id'],json={'understood':True,'title':'I understand'},headers=self.headers)
        self.assertTrue(response.json()['understood'])

    def test_screenshot_is_captured_once_when_batch_is_enqueued(self):
        self.service.busy=True
        def capture(folder):
            path=folder/'screen.png'; path.write_bytes(b'image'); return path
        with patch('web_api.screen.capture',side_effect=capture) as shot:
            nodes=self.child(questions=['A','B'],config={'selected_tab':'tab','screenshot':True})
            self.assertEqual(shot.call_count,1)
        for node in nodes:
            request=self.store.get(self.cid,node['id'],private=True)['request']
            self.assertTrue(request['attachments'][0].endswith('screen.png'))

    def test_large_ancestor_context_is_attached_without_truncation(self):
        from pathlib import Path
        self.service.busy=True
        text='Full context. '*6000+'END_OF_ROOT'
        self.store.update(self.cid,self.root_node['id'],answer=text)
        node=self.child()[0]
        request=self.store.get(self.cid,node['id'],private=True)['request']
        self.assertLess(len(request['prompt']),3000)
        context=json.loads(Path(request['attachments'][0]).read_text(encoding='utf-8'))
        self.assertEqual(context[0]['answer'],text)

    def test_database_restores_attachments_after_cache_files_are_lost(self):
        from pathlib import Path
        self.service.busy=True
        upload=self.client.post(f'/api/conversations/{self.cid}/files',headers=self.headers,
                               files={'file':('notes.txt',b'Database owns this content')}).json()
        nodes=self.child(questions=['First branch','Second branch'],files=[upload['path']])
        cached=Path(self.store.get(self.cid,nodes[0]['id'],private=True)['request']['attachments'][0])
        cached.unlink()
        self.service.file_path(self.cid,upload['path']).unlink()
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_assets').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM research_node_assets').fetchone()[0],2)
        def answer(prompt,files,*args,**kwargs):
            self.assertEqual(files[0].read_bytes(),b'Database owns this content')
            self.assertNotEqual(files[0],cached)
            return 'Restored answer'
        with patch('web_api.research.chatgpt.ask',side_effect=answer):
            self.service.busy=False
            self.wait_nodes(nodes)

    def test_stream_progress_survives_new_process_and_recovery(self):
        self.service.busy=True
        node=self.child(excerpt='Selected text')[0]
        self.assertTrue(self.store.claim(self.cid,node['id']))
        self.store.progress(self.cid,node['id'],partial='Durable partial answer',detail='Still generating')
        script="import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); c.row_factory=sqlite3.Row; print(json.dumps(dict(c.execute('SELECT * FROM research_nodes WHERE id=?',(sys.argv[2],)).fetchone())))"
        result=subprocess.run([sys.executable,'-c',script,str(self.service.store.db_path),node['id']],capture_output=True,text=True,check=True)
        persisted=json.loads(result.stdout)
        self.assertEqual(persisted['partial'],'Durable partial answer')
        self.assertEqual(persisted['excerpt'],'Selected text')
        self.assertEqual(json.loads(persisted['config'])['power'],2)
        with patch('caption.research_store.process_alive',return_value=False):
            recovered=ResearchStore(self.service.store)
        saved=recovered.get(self.cid,node['id'])
        self.assertEqual(saved['status'],'interrupted')
        self.assertEqual(saved['partial'],'Durable partial answer')

    def test_legacy_attachment_paths_are_backfilled_into_database(self):
        from pathlib import Path
        self.service.busy=True
        node=self.child()[0]
        path=self.root/'legacy.txt';path.write_bytes(b'Legacy snapshot')
        with self.service.store._connect() as db:
            db.execute('UPDATE research_nodes SET request=? WHERE id=?',
                       (json.dumps({'prompt':'Old prompt','attachments':[str(path)]}),node['id']))
        recovered=ResearchStore(self.service.store)
        path.unlink()
        files=recovered.restore_attachments(self.cid,node['id'],self.root/'restored')
        self.assertEqual(files[0].read_bytes(),b'Legacy snapshot')
        self.assertEqual(files[0].name,'legacy.txt')
        self.assertTrue(recovered.get(self.cid,node['id'],private=True)['request']['assets_in_database'])

    def test_missing_asset_rolls_back_node_and_asset_insertion(self):
        self.service.busy=True
        good=self.root/'good.txt';good.write_bytes(b'Valid file')
        child={'id':'atomic-node','title':'Atomic','question':'Do not partially save', 'excerpt':'',
               'config':{},'request':{'prompt':'Prompt','attachments':[str(good),str(self.root/'missing.txt')]}}
        with self.assertRaises(FileNotFoundError):
            self.store.add_children(self.cid,self.root_node,[child])
        self.assertEqual(len(self.store.list(self.cid)),1)
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_assets').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
