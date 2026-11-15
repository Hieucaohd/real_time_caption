import unittest

from caption.chatgpt_source import response_position


def conversation_mapping():
    mapping = {'root': {'parent': None, 'message': None}}
    previous = 'root'
    for index in range(1, 5):
        for role, prefix in (('user', 'u'), ('assistant', 'a')):
            key = f'{prefix}{index}'
            mapping[key] = {'parent': previous, 'message': {
                'id': key, 'author': {'role': role}, 'channel': 'final' if role == 'assistant' else None,
                'recipient': 'all', 'content': {'content_type': 'text'}, 'metadata': {}}}
            previous = key
    return mapping


class ResponsePositionTests(unittest.TestCase):
    def test_counts_answers_from_one_not_user_or_system_messages(self):
        mapping = conversation_mapping()
        for index in range(1, 5):
            self.assertEqual(response_position(mapping, f'a{index}'), index)
        self.assertIsNone(response_position(mapping, 'u2'))

    def test_excludes_reasoning_tools_hidden_messages_and_alternative_branches(self):
        mapping = conversation_mapping()
        previous = 'u4'
        for name, changes in (
            ('analysis', {'channel': 'analysis'}),
            ('tool_call', {'recipient': 'python'}),
            ('tool_result', {'author': {'role': 'tool'}}),
            ('commentary', {'channel': 'commentary'}),
            ('hidden', {'metadata': {'is_visually_hidden_from_conversation': True}}),
        ):
            message = {**mapping['a4']['message'], 'id': name, **changes}
            mapping[name] = {'parent': previous, 'message': message}
            previous = name
        mapping['a4']['parent'] = previous
        mapping['alternative'] = {'parent': 'u4', 'message': {**mapping['a4']['message'], 'id': 'alternative'}}
        self.assertEqual(response_position(mapping, 'a4'), 4)
        self.assertEqual(response_position(mapping, 'alternative'), 4)

    def test_unknown_or_incomplete_history_is_not_guessed(self):
        self.assertIsNone(response_position(None, 'a4'))
        self.assertIsNone(response_position(conversation_mapping(), 'missing'))
        mapping = conversation_mapping()
        del mapping['a2']
        self.assertIsNone(response_position(mapping, 'a4'))
        mapping = conversation_mapping()
        mapping['root']['parent'] = 'a4'
        self.assertIsNone(response_position(mapping, 'a4'))
        mapping = conversation_mapping()
        del mapping['root']['parent']
        self.assertIsNone(response_position(mapping, 'a4'))
