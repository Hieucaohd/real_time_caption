"""The delivery callback must precede answer waiting, but follow send confirmation."""
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch
from caption import chatgpt


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.page, self.composer, self.send, self.ack = Mock(), Mock(), Mock(), Mock()
        self.stack.enter_context(patch('playwright.sync_api.sync_playwright'))
        request = self.stack.enter_context(patch.object(chatgpt, '_request_page'))
        request.return_value.__enter__.return_value = (self.page, False)
        for name, result in [('_visible', self.composer), ('_composer_form', Mock()),
                             ('_wait_not_answering', None), ('_clear_attachments', 0),
                             ('_wait_send_enabled', self.send), ('_turn_ids', []),
                             ('_answered_turn_ids', []), ('_wait_stable', None)]:
            self.stack.enter_context(patch.object(chatgpt, name, return_value=result))
        self.wait = self.stack.enter_context(patch.object(chatgpt, '_wait_new_answer', return_value='new-turn'))
        self.stack.enter_context(patch.object(chatgpt, '_read_answer', return_value='Answer'))

    def ask(self, **kwargs):
        return chatgpt.ask('Question', selected_tab='existing', new_chat=False, on_sent=self.ack, **kwargs)

    def test_acknowledges_before_waiting_even_if_answer_is_cancelled(self):
        def waiting(*args):
            self.ack.assert_called_once_with()
            self.page.wait_for_function.assert_called_once()
            raise chatgpt.ChatGPTCancelled('Stopped while waiting')
        self.wait.side_effect = waiting
        with self.assertRaises(chatgpt.ChatGPTCancelled):
            self.ask()
        self.send.click.assert_called_once()

    def test_no_acknowledgement_if_send_was_not_confirmed(self):
        self.page.wait_for_function.side_effect = TimeoutError('No submitted turn')
        with self.assertRaises(chatgpt.ChatGPTError):
            self.ask()
        self.ack.assert_not_called()
        self.wait.assert_not_called()

    def test_dry_run_does_not_consume_transcript(self):
        self.assertEqual(self.ask(dry_run=True), '')
        self.send.click.assert_not_called()
        self.ack.assert_not_called()

    def test_successful_answer_acknowledges_exactly_once(self):
        self.assertEqual(self.ask(), 'Answer')
        self.ack.assert_called_once_with()
