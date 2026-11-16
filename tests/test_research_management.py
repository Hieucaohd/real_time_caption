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
            kwargs['on_response_source'](dict(response_conversation_url='https://chatgpt.com/c/branch',
                                             response_turn_id='branch-turn', response_message_id='branch-message', response_position=7))
            return 'Answer'
        with patch('web_api.research.chatgpt.ask', side_effect=answer):
            node = self.child()[0]
            done = self.wait_nodes([node])[0]
        self.assertEqual(done['response_tab_title'], 'Renamed after response')
        self.assertEqual(ResearchStore(self.service.store).get(self.cid, node['id'])['response_tab_title'], 'Renamed after response')
        reopened = ResearchStore(self.service.store).get(self.cid, node['id'])
        for key, expected in dict(response_conversation_url='https://chatgpt.com/c/branch',
                                  response_turn_id='branch-turn', response_message_id='branch-message', response_position=7).items():
            self.assertEqual(done[key], expected)
            self.assertEqual(reopened[key], expected)

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
            for name in ('response_conversation_url', 'response_turn_id', 'response_message_id', 'response_position'):
                db.execute(f'ALTER TABLE research_nodes DROP COLUMN {name}')
        reopened = ResearchStore(self.service.store)
        node = reopened.get(self.cid, self.root_node['id'])
        self.assertEqual(node['answer'], self.root_node['answer'])
        self.assertFalse(node['hidden'])
        self.assertEqual(node['response_tab_title'], '')
        self.assertEqual(node['response_conversation_url'], '')
        self.assertEqual(node['response_turn_id'], '')
        self.assertEqual(node['response_message_id'], '')
        self.assertIsNone(node['response_position'])

    def test_manual_source_syncs_chat_and_imported_root_but_not_children(self):
        self.service.busy = True
        child = self.child()[0]
        self.store.update(self.cid, child['id'], status='done', answer='Child answer')
        source = dict(response_conversation_url=' https://chatgpt.com/g/project/c/original?foo=bar#fragment ',
                      response_position=4)
        url = f'/api/conversations/{self.cid}/answers/{self.mid}/source'
        result = self.client.put(url, json=source, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        expected = dict(response_conversation_url='https://chatgpt.com/g/project/c/original?foo=bar#fragment',
                        response_position=4)
        self.assertEqual(result.json(), expected)
        for key, value in expected.items():
            self.assertEqual(getattr(self.service.store.messages(self.cid)[-1], key), value)
            self.assertEqual(ResearchStore(self.service.store).get(self.cid, self.root_node['id'])[key], value)
            self.assertEqual(self.store.get(self.cid, child['id'])[key], None if key == 'response_position' else '')
        partial = dict(response_conversation_url='https://chatgpt.com/c/corrected')
        result = self.client.put(self.base + '/' + self.root_node['id'] + '/source', json=partial, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        message = self.service.store.messages(self.cid)[-1]
        self.assertEqual(message.response_conversation_url, partial['response_conversation_url'])
        self.assertIsNone(message.response_position)
        self.client.put(self.base + '/' + child['id'] + '/source', json=expected, headers=self.headers)
        self.assertEqual(self.service.store.messages(self.cid)[-1].response_conversation_url, partial['response_conversation_url'])
        self.assertEqual(self.store.get(self.cid, self.root_node['id'])['answer'], self.root_node['answer'])

    def test_manual_source_validates_scope_roles_and_urls_without_partial_writes(self):
        url = f'/api/conversations/{self.cid}/answers/{self.mid}/source'
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        source = dict(response_conversation_url='https://chatgpt.com/c/valid', response_position=1)
        self.assertEqual(self.client.put(url.replace(self.cid, other), json=source, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.put(self.base.replace(self.cid, other) + '/' + self.root_node['id'] + '/source', json=source, headers=self.headers).status_code, 404)
        user_mid = self.service.store.messages(self.cid)[0].id
        self.assertEqual(self.client.put(f'/api/conversations/{self.cid}/answers/{user_mid}/source', json=source, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.put(url, json=source).status_code, 403)
        for value in ('javascript:alert(1)', 'https://evil.test/c/id', 'https://chatgpt.com/', 'https://user:pass@chatgpt.com/c/id'):
            self.assertEqual(self.client.put(url, json={**source, 'response_conversation_url': value}, headers=self.headers).status_code, 422)
        for value in (0, -1, 1.5, '4', True, 2147483648):
            self.assertEqual(self.client.put(url, json={**source, 'response_position': value}, headers=self.headers).status_code, 422)
        self.assertEqual(self.store.get(self.cid, self.root_node['id'])['response_conversation_url'], '')
        self.assertEqual(self.service.store.messages(self.cid)[-1].response_conversation_url, '')

    def test_create_chatgpt_branch_uses_saved_source_without_changing_parent(self):
        url = 'https://chatgpt.com/g/project/c/original?keep=yes'
        self.store.update_response_source(self.cid, {'response_conversation_url': url, 'response_position': 3}, nid=self.root_node['id'])
        before = self.store.get(self.cid, self.root_node['id'])
        tab = {'key': 'branch-tab', 'title': 'Branch', 'label': 'Branch', 'url': 'https://chatgpt.com/c/new', 'focused': True}
        def create(link, position):
            self.assertTrue(self.service.busy)
            self.assertEqual((link, position), (url, 3))
            return tab
        with patch('caption.chatgpt_branch.create_branch', side_effect=create) as branch:
            result = self.post(self.base + '/' + self.root_node['id'] + '/chatgpt-branch')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json(), tab)
            branch.assert_called_once()
        self.assertFalse(self.service.busy)
        self.assertEqual(self.store.get(self.cid, self.root_node['id']), before)
        self.assertEqual(len(self.store.list(self.cid)), 1)

    def test_chatgpt_branch_validation_busy_scope_and_failure_release(self):
        endpoint = self.base + '/' + self.root_node['id'] + '/chatgpt-branch'
        with patch('caption.chatgpt_branch.create_branch') as branch:
            self.assertEqual(self.post(endpoint).status_code, 400)
            other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
            self.assertEqual(self.post(endpoint.replace(self.cid, other)).status_code, 404)
            self.assertEqual(self.client.post(endpoint).status_code, 403)
            self.store.update_response_source(self.cid, {'response_conversation_url': 'https://chatgpt.com/c/source', 'response_position': 1}, nid=self.root_node['id'])
            self.service.busy = True
            self.assertEqual(self.post(endpoint).status_code, 409)
            branch.assert_not_called()
            self.service.busy = False
            branch.side_effect = ValueError('History unavailable')
            self.assertEqual(self.post(endpoint).status_code, 400)
            self.assertFalse(self.service.busy)

    def test_open_response_uses_saved_source_and_does_not_change_node_or_busy_work(self):
        url = 'https://chatgpt.com/g/project/c/source?keep=yes#saved'
        nid = self.root_node['id']
        endpoint = self.base + '/' + nid + '/open-chatgpt'
        self.assertEqual(self.post(endpoint).status_code, 400)  # No saved source.
        self.store.update_response_source(self.cid, {'response_conversation_url': url, 'response_position': 3}, nid=nid)
        before = self.store.get(self.cid, nid)
        self.service.busy = True
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        with patch('caption.chatgpt_navigation.open_response', return_value={'key': 'new', 'url': url, 'response_position': 3}) as opened:
            self.assertEqual(self.client.post(endpoint).status_code, 403)
            self.assertEqual(self.post(endpoint.replace(self.cid, other)).status_code, 404)
            child = self.child()[0]
            self.assertEqual(self.post(self.base + '/' + child['id'] + '/open-chatgpt').status_code, 400)
            opened.assert_not_called()
            result = self.post(endpoint)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['response_position'], 3)
            opened.assert_called_once_with(url, 3)
        self.assertTrue(self.service.busy)
        self.assertEqual(self.store.get(self.cid, nid), before)


if __name__ == '__main__':
    unittest.main()
