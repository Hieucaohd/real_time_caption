import unittest
from unittest.mock import patch

from caption.research_store import ResearchStore
import test_research as fixtures


class ReadingMarkTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    post = fixtures.ResearchTests.post

    def save(self, mark, cid=None):
        return self.client.put(f'/api/conversations/{cid or self.cid}/research/{self.root_node["id"]}/reading-mark',
                               json={'mark': mark}, headers=self.headers)

    def test_mark_replaces_clears_and_survives_store_reopen(self):
        mark = {'start': 2, 'end': 12, 'text': 'Read here'}
        self.assertEqual(self.save(mark).status_code, 200)
        reopened = ResearchStore(self.service.store)
        self.assertEqual(reopened.get(self.cid, self.root_node['id'])['reading_mark'], mark)
        self.assertEqual(reopened.get(self.cid, self.root_node['id'])['answer'], self.root_node['answer'])
        replacement = {'start': 20, 'end': 30, 'text': 'Next mark'}
        self.assertEqual(self.save(replacement).json()['reading_mark'], replacement)
        self.assertIsNone(self.save(None).json()['reading_mark'])
        self.assertIsNone(reopened.get(self.cid, self.root_node['id'])['reading_mark'])

    def test_marks_are_scoped_and_reject_invalid_ranges(self):
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        mark = {'start': 0, 'end': 4, 'text': 'Read'}
        self.assertEqual(self.save(mark, other).status_code, 404)
        for change in ({'start': -1}, {'end': 0}, {'start': 5}, {'text': ''}, {'text': 'x' * 20001}):
            self.assertEqual(self.save({**mark, **change}).status_code, 422)
        self.assertIsNone(self.store.get(self.cid, self.root_node['id'])['reading_mark'])

    def test_selected_excerpt_marks_parent_when_children_are_created(self):
        self.service.busy = True
        mark = {'start': 14, 'end': 19, 'text': 'alpha'}
        endpoint = self.base + '/' + self.root_node['id'] + '/children'
        body = {'questions': ['Explain alpha', 'Give an example'], 'excerpt': 'alpha',
                'parent_reading_mark': mark, 'config': {'selected_tab': 'test-tab'}}
        response = self.post(endpoint, body)
        self.assertEqual(response.status_code, 200, response.text)
        parent = ResearchStore(self.service.store).get(self.cid, self.root_node['id'])
        self.assertEqual(parent['reading_mark'], mark)
        self.assertEqual(parent['answer'], self.root_node['answer'])
        self.assertEqual(len(response.json()), 2)
        for child in response.json():
            self.assertEqual(child['excerpt'], 'alpha')
            self.assertIsNone(child['reading_mark'])
        # Creating another child without a selection leaves the parent's mark alone.
        self.assertEqual(self.post(endpoint, {'questions': ['Another question'], 'config': {'selected_tab': 'test-tab'}}).status_code, 200)
        self.assertEqual(self.store.get(self.cid, parent['id'])['reading_mark'], mark)
        for invalid in ({**mark, 'start': -1}, {**mark, 'text': 'different excerpt'}):
            self.assertEqual(self.post(endpoint, {**body, 'parent_reading_mark': invalid}).status_code, 422)
        self.assertEqual(self.store.get(self.cid, parent['id'])['reading_mark'], mark)

    def test_failed_batch_rolls_back_children_and_keeps_previous_mark(self):
        self.service.busy = True
        old = {'start': 0, 'end': 4, 'text': 'Root'}
        self.save(old)
        archive = self.store._archive_assets
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError('Attachment failed')
            return archive(*args)
        with patch.object(self.store, '_archive_assets', side_effect=fail_second):
            response = self.post(self.base + '/' + self.root_node['id'] + '/children', {
                'questions': ['One', 'Two'], 'excerpt': 'alpha',
                'parent_reading_mark': {'start': 14, 'end': 19, 'text': 'alpha'},
                'config': {'selected_tab': 'test-tab'}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.store.list(self.cid)), 1)
        self.assertEqual(self.store.get(self.cid, self.root_node['id'])['reading_mark'], old)


if __name__ == '__main__':
    unittest.main()
