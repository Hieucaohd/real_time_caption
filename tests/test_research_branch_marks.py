import unittest

from caption.research_store import ResearchStore
import test_research as fixtures


class BranchMarkTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    tearDown = fixtures.ResearchTests.tearDown
    post = fixtures.ResearchTests.post
    child = fixtures.ResearchTests.child

    def marked_children(self, text, questions):
        start = self.root_node['answer'].index(text)
        mark = dict(start=start, end=start + len(text), text=text)
        return self.child(excerpt=text, parent_reading_mark=mark, questions=questions), mark

    def test_many_source_marks_persist_independently_of_reading_mark(self):
        self.service.busy = True
        alpha, alpha_mark = self.marked_children('alpha', ['Explain alpha', 'Alpha example'])
        beta, beta_mark = self.marked_children('beta', ['Explain beta'])
        reopened = ResearchStore(self.service.store)
        root_id = self.root_node['id']
        marks = reopened.branch_marks(self.cid, root_id)
        self.assertEqual([item['id'] for item in marks], [n['id'] for n in alpha + beta])
        self.assertEqual([item['mark'] for item in marks], [alpha_mark, alpha_mark, beta_mark])
        self.assertEqual(reopened.get(self.cid, root_id)['reading_mark'], beta_mark)
        self.store.update(self.cid, root_id, reading_mark=None)
        self.assertEqual(reopened.branch_marks(self.cid, root_id), marks)
        for n in alpha + beta:
            self.assertEqual(reopened.branch_marks(self.cid, n['id']), [])
        detail = self.client.get(self.base + '/' + root_id).json()
        self.assertEqual(detail['branch_marks'], marks)
        self.assertEqual(detail['answer'], self.root_node['answer'])
        self.assertIsNone(detail['reading_mark'])

    def test_names_visibility_retry_and_deletion_follow_the_linked_children(self):
        self.service.busy = True
        children, mark = self.marked_children('alpha', ['One', 'Two'])
        first, second = children
        self.store.update(self.cid, first['id'], title='Renamed', status='error')
        self.service.research.retry(self.cid, first['id'])
        self.store.set_hidden(self.cid, first['id'], True)
        marks = self.store.branch_marks(self.cid, self.root_node['id'])
        self.assertEqual(marks[0]['title'], 'Renamed')
        self.assertTrue(marks[0]['hidden'])
        self.assertEqual(marks[0]['mark'], mark)
        self.service.research.delete(self.cid, first['id'])
        self.assertEqual([n['id'] for n in self.store.branch_marks(self.cid, self.root_node['id'])], [second['id']])
        other = self.post('/api/conversations', {'title': 'Other'}).json()['id']
        self.assertEqual(self.store.branch_marks(other, self.root_node['id']), [])
        self.service.research.delete(self.cid, second['id'])
        self.assertEqual(self.store.branch_marks(self.cid, self.root_node['id']), [])

    def test_legacy_excerpt_is_available_without_inventing_an_offset(self):
        self.service.busy = True
        child = self.child(excerpt='alpha')[0]
        marks = ResearchStore(self.service.store).branch_marks(self.cid, self.root_node['id'])
        self.assertEqual(marks, [dict(id=child['id'], title=child['title'], hidden=False, excerpt='alpha', mark=None)])
