import threading
import time
import unittest
from unittest.mock import patch

from caption.research_store import ResearchStore
import test_research as research_fixtures


class ResearchManagementTests(unittest.TestCase):
    setUp = research_fixtures.ResearchTests.setUp
    tearDown = research_fixtures.ResearchTests.tearDown
    post = research_fixtures.ResearchTests.post
    child = research_fixtures.ResearchTests.child
    wait_nodes = research_fixtures.ResearchTests.wait_nodes

    def tree(self, files=None):
        self.service.busy = True
        a, b = self.child(questions=['First branch', 'Sibling'], files=files or [])
        self.store.update(self.cid, a['id'], status='done', answer='Parent answer')
        c = self.child(parent=a['id'], questions=['Grandchild'], files=files or [])[0]
        return a, b, c

    def visibility(self, nid, hidden):
        return self.client.put(self.base + '/' + nid + '/visibility', json={'hidden': hidden}, headers=self.headers)

    def delete(self, nid, cid=None):
        return self.client.delete(f'/api/conversations/{cid or self.cid}/research/{nid}', headers=self.headers)

    def test_hide_restore_and_new_children_persist_in_database(self):
        a, b, c = self.tree()
        self.assertEqual(self.visibility(a['id'], True).status_code, 200)
        reopened = ResearchStore(self.service.store)
        self.assertTrue(reopened.get(self.cid, a['id'])['hidden'])
        self.assertTrue(reopened.get(self.cid, c['id'])['hidden'])
        self.assertFalse(reopened.get(self.cid, b['id'])['hidden'])
        self.assertEqual(reopened.get(self.cid, c['id'])['status'], 'queued')
        d = self.child(parent=a['id'])[0]
        self.assertTrue(d['hidden'])
        self.visibility(c['id'], False)
        self.assertFalse(self.store.get(self.cid, a['id'])['hidden'])
        self.assertFalse(self.store.get(self.cid, c['id'])['hidden'])
        self.assertTrue(self.store.get(self.cid, d['id'])['hidden'])
        self.visibility(a['id'], False)
        self.assertFalse(self.store.get(self.cid, d['id'])['hidden'])

    def test_delete_descendants_assets_layout_and_keep_source_chat(self):
        upload = self.client.post(f'/api/conversations/{self.cid}/files', headers=self.headers,
                                  files={'file': ('notes.txt', b'shared attachment')}).json()
        a, b, c = self.tree([upload['path']])
        rid = self.root_node['id']
        self.store.save_graph_layout(self.cid, rid, {'positions': {n['id']: {'x': i * 50, 'y': 0}
            for i, n in enumerate((a, b, c))}, 'collapsed': [a['id'], c['id']]})
        response = self.delete(a['id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()['ids']), {a['id'], c['id']})
        layout = self.store.graph_layout(self.cid, rid)
        self.assertEqual(set(layout['positions']), {b['id']})
        self.assertEqual(layout['collapsed'], [])
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_assets').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM research_node_assets').fetchone()[0], 1)
        self.assertEqual(self.delete(rid).status_code, 200)
        self.assertEqual(self.store.list(self.cid), [])
        with self.service.store._connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_assets').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM research_graph_layouts').fetchone()[0], 0)
        self.assertEqual(len(self.service.store.messages(self.cid)), 2)
        self.assertNotEqual(self.store.import_answer(self.cid, self.mid)['id'], rid)

    def test_mutations_are_scoped_and_require_local_client_header(self):
        a, _, _ = self.tree()
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        self.assertEqual(self.delete(a['id'], other).status_code, 404)
        self.assertEqual(self.client.put(f'/api/conversations/{other}/research/{a["id"]}/visibility',
            json={'hidden': True}, headers=self.headers).status_code, 404)
        self.assertFalse(self.store.get(self.cid, a['id'])['hidden'])
        self.assertEqual(self.client.delete(self.base + '/' + a['id']).status_code, 403)

    def test_deleting_working_branch_cancels_without_resurrection(self):
        started, release, returned = threading.Event(), threading.Event(), threading.Event()
        def answer(*args, **kwargs):
            started.set()
            release.wait(4)
            self.assertTrue(kwargs['cancel_event'].is_set())
            kwargs['on_partial']('Late partial')
            kwargs['on_response_tab']('Late tab')
            returned.set()
            return 'Late answer'
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            node = self.child()[0]
            self.assertTrue(started.wait(3))
            response = self.delete(self.root_node['id'])
            self.assertEqual(response.status_code, 200, response.text)
            release.set()
            self.assertTrue(returned.wait(3))
            deadline = time.monotonic() + 3
            while self.service.busy and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(self.service.busy)
        self.assertEqual(self.store.list(self.cid), [])
        self.assertEqual(self.service.jobs['research-' + node['id']]['status'], 'cancelled')

    def test_response_tab_title_comes_from_completed_response_and_survives_restart(self):
        def answer(*args, **kwargs):
            kwargs['on_response_tab']('Renamed after response')
            return 'Answer'
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            node = self.child()[0]
            done = self.wait_nodes([node])[0]
        self.assertEqual(done['response_tab_title'], 'Renamed after response')
        self.assertEqual(ResearchStore(self.service.store).get(self.cid, node['id'])['response_tab_title'], 'Renamed after response')

    def test_root_import_preserves_chat_response_tab_title(self):
        mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Another answer', response_tab_title='Original Chat tab')
        root = self.store.import_answer(self.cid, mid)
        self.assertEqual(root['response_tab_title'], 'Original Chat tab')
        self.assertEqual(self.root_node['response_tab_title'], '')

    def test_schema_upgrade_keeps_old_answers(self):
        self.service.busy = True
        with self.service.store._connect() as db:
            db.execute('ALTER TABLE research_nodes DROP COLUMN hidden')
            db.execute('ALTER TABLE research_nodes DROP COLUMN response_tab_title')
        reopened = ResearchStore(self.service.store)
        node = reopened.get(self.cid, self.root_node['id'])
        self.assertEqual(node['answer'], self.root_node['answer'])
        self.assertFalse(node['hidden'])
        self.assertEqual(node['response_tab_title'], '')


if __name__ == '__main__':
    unittest.main()
