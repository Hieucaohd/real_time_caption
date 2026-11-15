import unittest
from pathlib import Path
from unittest.mock import patch

from caption.research_store import ResearchStore
import test_research as fixtures


class AncestorContextTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    post = fixtures.ResearchTests.post
    child = fixtures.ResearchTests.child
    wait_nodes = fixtures.ResearchTests.wait_nodes

    def chain(self):
        self.service.busy = True
        self.store.update(self.cid, self.root_node['id'], answer='ROOT_ANSWER')
        chain = [self.store.get(self.cid, self.root_node['id'])]
        for index in range(1, 4):
            node = self.child(parent=chain[-1]['id'], questions=[f'QUESTION_{index}'])[0]
            chain.append(self.store.update(self.cid, node['id'], status='done', answer=f'ANSWER_{index} ' + r'\[x_1 = 2\]'))
        sibling = self.child(questions=['SIBLING_QUESTION'])[0]
        self.store.update(self.cid, sibling['id'], status='done', answer='SIBLING_ANSWER')
        return chain

    def test_depth_selection_is_exact_ordered_and_excludes_other_branches(self):
        chain = self.chain()
        for depth in (0, 1, 2, 3, -1, 99):
            with self.subTest(depth=depth):
                node = self.child(parent=chain[-1]['id'], config={'selected_tab': 'tab', 'ancestor_depth': depth})[0]
                saved = self.store.get(self.cid, node['id'], private=True)
                chosen = chain if depth == -1 else chain[-depth:] if depth else []
                self.assertEqual(saved['config']['ancestor_depth'], depth)
                self.assertEqual(saved['request']['ancestor_ids'], [n['id'] for n in chosen])
                self.assertEqual(saved['request']['prompt'], 'Question: Explain alpha')
                files = self.store.restore_attachments(self.cid, node['id'], self.root / node['id'])
                if not chosen:
                    self.assertEqual(files, [])
                    continue
                self.assertEqual(len(files), 1)
                text = files[0].read_text(encoding='utf-8')
                positions = []
                for ancestor in chosen:
                    self.assertIn(ancestor['question'], text)
                    self.assertIn(ancestor['answer'], text)
                    positions.append(text.index(ancestor['answer']))
                self.assertEqual(positions, sorted(positions))
                for excluded in chain[:-len(chosen)]:
                    self.assertNotIn(excluded['answer'], text)
                self.assertNotIn('SIBLING_', text)

    def test_batch_snapshot_is_saved_in_sqlite_and_retry_uses_original_content(self):
        chain = self.chain()
        nodes = self.child(parent=chain[-1]['id'], questions=['First', 'Second'],
                           config={'selected_tab': 'tab', 'ancestor_depth': 2})
        saved = self.store.get(self.cid, nodes[0]['id'], private=True)
        file = Path(saved['request']['attachments'][0])
        original = file.read_bytes()
        file.unlink()
        for ancestor in chain:
            self.store.update(self.cid, ancestor['id'], answer='Changed after enqueue')
        reopened = ResearchStore(self.service.store)
        self.assertEqual(reopened.restore_attachments(self.cid, nodes[0]['id'], self.root / 'recovered')[0].read_bytes(), original)
        with self.service.store._connect() as db:
            assets = db.execute('SELECT asset_id FROM research_node_assets WHERE node_id IN (?, ?)', tuple(n['id'] for n in nodes)).fetchall()
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[0]['asset_id'], assets[1]['asset_id'])
        self.store.update(self.cid, nodes[1]['id'], status='cancelled')
        def fail(*args, **kwargs):
            self.assertEqual(args[1][0].read_bytes(), original)
            raise RuntimeError('Retry fixture')
        with patch('web_api.research.chatgpt.ask', side_effect=fail):
            self.service.busy = False
            self.wait_nodes(nodes[:1], 'error')
        def succeed(*args, **kwargs):
            self.assertEqual(args[1][0].read_bytes(), original)
            return 'Answer'
        with patch('web_api.research.chatgpt.ask', side_effect=succeed):
            self.post(self.base + '/' + nodes[0]['id'] + '/retry')
            self.wait_nodes(nodes[:1])

    def test_preferences_validation_and_conversation_scope(self):
        self.service.busy = True
        result = self.client.put('/api/research/defaults', json={'ancestor_depth': -1}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(ResearchStore(self.service.store).defaults()['ancestor_depth'], -1)
        endpoint = self.base + '/' + self.root_node['id'] + '/children'
        for depth in (-2, 10001, 1.5, True, '2'):
            self.assertEqual(self.post(endpoint, {'questions': ['Question'], 'config': {'ancestor_depth': depth}}).status_code, 422)
            self.assertEqual(self.client.put('/api/research/defaults', json={'ancestor_depth': depth}, headers=self.headers).status_code, 422)
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        self.assertEqual(self.post(endpoint.replace(self.cid, other), {'questions': ['Question'],
                         'config': {'selected_tab': 'tab', 'ancestor_depth': -1}}).status_code, 404)
