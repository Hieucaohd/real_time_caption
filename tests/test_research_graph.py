import unittest

import test_web_api as fixtures
from caption.research_store import ResearchStore


class ResearchGraphTests(unittest.TestCase):
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.service.busy = True
        self.service.store.add_message(self.cid, 'user', 'You', 'Graph question')
        mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Graph answer')
        self.root_node = self.service.research.store.import_answer(self.cid, mid)
        self.nodes = self.service.research.children(self.cid, self.root_node['id'],
            {'questions': ['First', 'Second'], 'config': {'selected_tab': 'test'}})
        self.url = f'/api/conversations/{self.cid}/research/{self.root_node["id"]}/layout'

    def save(self, values):
        return self.client.patch(self.url, json=values, headers=self.headers)

    def test_coordinates_camera_and_preferences_persist_after_store_reopens(self):
        values = {'positions': {self.nodes[0]['id']: {'x': -200.25, 'y': 507.5}},
                  'camera': {'x': -150.5, 'y': 600, 'zoom': 1.75},
                  'collapsed': [self.root_node['id']], 'snap': False, 'wheel_zoom': False,
                  'mode': 'hand', 'move_subtree': True}
        response = self.save(values)
        self.assertEqual(response.status_code, 200, response.text)
        reopened = ResearchStore(self.service.store).graph_layout(self.cid, self.root_node['id'])
        for key, value in values.items():
            self.assertEqual(reopened[key], value)
        self.assertEqual(reopened['revision'], 1)

    def test_position_patches_merge_without_losing_other_nodes_or_view(self):
        a, b = [n['id'] for n in self.nodes]
        self.save({'positions': {a: {'x': 10, 'y': 20}}, 'camera': {'x': 40, 'y': 50, 'zoom': .5}})
        self.save({'positions': {b: {'x': 30, 'y': 40}}})
        result = self.save({'positions': {a: {'x': 500, 'y': -30}}, 'snap': False}).json()
        self.assertEqual(result['positions'], {a: {'x': 500, 'y': -30}, b: {'x': 30, 'y': 40}})
        self.assertEqual(result['camera'], {'x': 40, 'y': 50, 'zoom': .5})
        self.assertEqual(result['revision'], 3)

    def test_rejects_cross_tree_positions_and_collapsed_nodes_atomically(self):
        mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Another root')
        other = self.service.research.store.import_answer(self.cid, mid)
        response = self.save({'positions': {self.nodes[0]['id']: {'x': 10, 'y': 20}, other['id']: {'x': 30, 'y': 40}}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.save({'collapsed': [other['id']]}).status_code, 400)
        self.assertEqual(self.client.get(self.url).json()['positions'], {})
        self.assertEqual(self.client.get(self.url).json()['revision'], 0)

    def test_layout_cannot_reparent_nodes_or_change_answers(self):
        self.assertEqual(self.save({'parent_id': self.nodes[0]['id']}).status_code, 422)
        before = self.service.research.store.get(self.cid, self.nodes[0]['id'])
        self.save({'positions': {self.nodes[0]['id']: {'x': -50, 'y': -60}}})
        after = self.service.research.store.get(self.cid, self.nodes[0]['id'])
        self.assertEqual(before, after)

    def test_invalid_coordinates_and_nonroot_layouts_are_rejected(self):
        for camera in ({'x': 'NaN', 'y': 0, 'zoom': 1}, {'x': 0, 'y': 0, 'zoom': 0}, {'x': 0, 'y': 0, 'zoom': 5}):
            self.assertEqual(self.save({'camera': camera}).status_code, 422)
        self.assertEqual(self.save({'positions': {self.nodes[0]['id']: {'x': 1e9, 'y': 0}}}).status_code, 422)
        self.assertEqual(self.client.get(self.url.replace(self.root_node['id'], self.nodes[0]['id'])).status_code, 400)
        other = self.post('/api/conversations', {'title':'Other'}).json()['id']
        self.assertEqual(self.client.get(self.url.replace(self.cid, other)).status_code, 404)


if __name__ == '__main__':
    unittest.main()
