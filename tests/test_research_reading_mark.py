import unittest

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


if __name__ == '__main__':
    unittest.main()
