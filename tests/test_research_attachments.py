from pathlib import Path
import unittest

import test_conversation_archive as archive_fixtures


class ResearchAttachmentTests(unittest.TestCase):
    setUp = archive_fixtures.ConversationArchiveTests.setUp
    tearDown = archive_fixtures.ConversationArchiveTests.tearDown
    post = archive_fixtures.ConversationArchiveTests.post
    exported = archive_fixtures.ConversationArchiveTests.exported
    upload = archive_fixtures.ConversationArchiveTests.upload

    def url(self, nid, suffix=''):
        return f'/api/conversations/{self.cid}/research/{nid}{suffix}'

    def test_frozen_files_visible_without_source_or_retry_cache(self):
        request = self.research.get(self.cid, self.child['id'], private=True)['request']
        self.photo.unlink()
        for value in request['attachments']:
            path = Path(value)
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
            path.unlink()
        result = self.client.get(self.url(self.child['id'])).json()
        self.assertNotIn('request', result)
        self.assertEqual(len(result['attachments']), 1)
        attachment = result['attachments'][0]
        self.assertTrue(attachment['available'])
        self.assertEqual(attachment['type'], 'image/png')
        self.assertEqual(attachment['size'], len(b'image bytes'))
        self.assertNotIn('local_path', attachment)
        response = self.client.get(self.url(self.child['id'], '/attachments/0'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'image bytes')
        self.assertIn('image/png', response.headers['content-type'])
        self.assertIn("filename*=UTF-8''", response.headers['content-disposition'])
        self.research.update(self.cid, self.child['id'], status='cancelled')
        self.assertEqual(self.client.get(self.url(self.child['id'], '/attachments/0')).content, b'image bytes')

    def test_roots_show_source_question_files_and_report_missing_files(self):
        result = self.client.get(self.url(self.node['id'])).json()
        self.assertEqual(len(result['attachments']), 2)  # Deduplicate image used as both attachment and file.
        self.assertEqual(self.client.get(self.url(self.node['id'], '/attachments/1')).content, b'external attachment')
        self.photo.unlink()
        result = self.client.get(self.url(self.node['id'])).json()
        self.assertFalse(result['attachments'][0]['available'])
        self.assertEqual(self.client.get(self.url(self.node['id'], '/attachments/0')).status_code, 404)
        self.assertEqual(result['answer'], 'Alpha concept. $x^2$')

    def test_access_is_scoped_to_node_conversation_and_position(self):
        other = self.store.create('Other')
        self.assertEqual(self.client.get(self.url(self.child['id'], '/attachments/0').replace(self.cid, other.id)).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.child['id'], '/attachments/-1')).status_code, 404)
        self.assertEqual(self.client.get(self.url(self.child['id'], '/attachments/99')).status_code, 404)
        self.service.research.delete(self.cid, self.child['id'])
        self.assertEqual(self.client.get(self.url(self.child['id'], '/attachments/0')).status_code, 404)

    def test_attachments_survive_portable_import_including_chat_roots(self):
        result = self.upload(self.exported())
        self.assertEqual(result.status_code, 200, result.text)
        cid = result.json()['id']
        nodes = self.research.list(cid)
        root = next(node for node in nodes if node['parent_id'] is None)
        child = next(node for node in nodes if node['parent_id'] == root['id'])
        for node, count in ((root, 2), (child, 1)):
            base = f'/api/conversations/{cid}/research/{node["id"]}'
            metadata = self.client.get(base).json()['attachments']
            self.assertEqual(len(metadata), count)
            self.assertTrue(all(item['available'] for item in metadata))
            self.assertEqual(self.client.get(base + '/attachments/0').content, b'image bytes')

    def test_legacy_snapshot_can_be_read_without_exposing_its_path(self):
        file = self.root / 'legacy-note.txt'
        file.write_bytes(b'legacy content')
        self.research.update(self.cid, self.grand['id'], request={'prompt':'Old prompt', 'attachments':[str(file)]})
        with self.store._connect() as db:
            db.execute('DELETE FROM research_node_assets WHERE node_id=?', (self.grand['id'],))
        base = self.url(self.grand['id'])
        metadata = self.client.get(base).json()['attachments']
        self.assertEqual(metadata[0]['name'], file.name)
        self.assertNotIn(str(self.root), str(metadata))
        self.assertEqual(self.client.get(base + '/attachments/0').content, b'legacy content')


if __name__ == '__main__': unittest.main()
