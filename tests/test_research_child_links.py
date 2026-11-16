import unittest

import test_conversation_archive as fixtures


class ChildLinkTests(unittest.TestCase):
    setUp = fixtures.ConversationArchiveTests.setUp
    tearDown = fixtures.ConversationArchiveTests.tearDown
    post = fixtures.ConversationArchiveTests.post
    exported = fixtures.ConversationArchiveTests.exported
    upload = fixtures.ConversationArchiveTests.upload

    def link(self, parent=None, child=None, cid=None, body=None):
        return self.client.put(f'/api/conversations/{cid or self.cid}/research/'
                               f'{parent or self.node["id"]}/child-links/{child or self.child["id"]}',
                               headers=self.headers, json=body or {
                                   'excerpt': 'x²', 'mark': {'start': 15, 'end': 22, 'text': 'x2x^2x2'}})

    def test_link_preserves_answer_request_reading_mark_and_tree(self):
        old = self.research.get(self.cid, self.child['id'], private=True)
        parent = self.research.get(self.cid, self.node['id'])
        response = self.link()
        self.assertEqual(response.status_code, 200, response.text)
        current = self.research.get(self.cid, self.child['id'], private=True)
        for key in ('question', 'answer', 'request', 'config', 'parent_id', 'hidden', 'status'):
            self.assertEqual(old[key], current[key], key)
        self.assertEqual(self.research.get(self.cid, self.node['id'])['reading_mark'], parent['reading_mark'])
        marks = self.research.branch_marks(self.cid, self.node['id'])
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]['excerpt'], 'x²')
        self.assertEqual(marks[0]['mark']['text'], 'x2x^2x2')  # KaTeX visible and raw text differ.
        self.assertEqual(self.link().status_code, 200)  # Idempotent, no duplicate marks/nodes/jobs.
        self.assertEqual(len(self.research.list(self.cid)), 3)
        self.assertEqual(len(self.research.branch_marks(self.cid, self.node['id'])), 1)

    def test_rejects_non_child_cross_conversation_missing_and_unfinished_parent(self):
        for child in (self.node['id'], self.grand['id']):
            self.assertEqual(self.link(child=child).status_code, 400)
        other = self.store.create('Other')
        self.assertEqual(self.link(cid=other.id).status_code, 404)
        self.assertEqual(self.link(child='missing').status_code, 404)
        self.research.update(self.cid, self.node['id'], status='working')
        self.assertEqual(self.link().status_code, 400)

    def test_validates_selection_and_roundtrips_portable_archive(self):
        for body in ({'excerpt': '', 'mark': {'start': 0, 'end': 1, 'text': 'a'}},
                     {'excerpt': 'a', 'mark': None},
                     {'excerpt': 'a', 'mark': {'start': 2, 'end': 1, 'text': 'a'}}):
            self.assertEqual(self.link(body=body).status_code, 422)
        self.assertEqual(self.link().status_code, 200)
        imported = self.upload(self.exported())
        self.assertEqual(imported.status_code, 200, imported.text)
        cid = imported.json()['id']
        root = next(n for n in self.research.list(cid) if n['parent_id'] is None)
        marks = self.research.branch_marks(cid, root['id'])
        self.assertEqual(marks[0]['excerpt'], 'x²')
        self.assertEqual(marks[0]['mark'], {'start': 15, 'end': 22, 'text': 'x2x^2x2'})
        self.assertNotEqual(marks[0]['id'], self.child['id'])
