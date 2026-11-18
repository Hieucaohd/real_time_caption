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
        self.context_check = self.stack.enter_context(patch.object(chatgpt, '_check_context_full'))
        self.fill = self.stack.enter_context(patch.object(chatgpt, '_fill_composer'))
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

    def test_context_full_before_fill_never_sends_or_consumes_transcript(self):
        self.context_check.side_effect = chatgpt.ChatGPTContextFull('Context window full')
        with self.assertRaises(chatgpt.ChatGPTContextFull):
            self.ask()
        self.fill.assert_not_called()
        self.send.click.assert_not_called()
        self.ack.assert_not_called()

    def test_fill_failure_does_not_expose_private_prompt_or_send(self):
        self.fill.side_effect = chatgpt.ChatGPTError('Could not prepare message; not sent')
        with self.assertRaises(chatgpt.ChatGPTError) as raised:
            self.ask()
        self.assertNotIn('PRIVATE TRANSCRIPT', str(raised.exception))
        self.send.click.assert_not_called()
        self.ack.assert_not_called()

    def test_context_full_while_waiting_keeps_specific_error_and_delivery_ack(self):
        self.wait.side_effect = chatgpt.ChatGPTContextFull('Context window full')
        with self.assertRaisesRegex(chatgpt.ChatGPTContextFull, 'Context window full'):
            self.ask()
        self.ack.assert_called_once_with()

    def test_attachment_and_send_wait_share_one_upload_budget(self):
        from pathlib import Path
        now = [0]
        def chips_ready(*args, **kwargs):
            now[0] = 380
        with patch.object(chatgpt.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(chatgpt, '_wait_attachments_visible', side_effect=chips_ready) as chips, \
                patch.object(chatgpt, '_wait_send_enabled', return_value=self.send) as ready:
            self.ask(file_path=Path('image.png'), upload_timeout_s=600)
        self.assertEqual(chips.call_args.args[2], 600)
        self.assertEqual(ready.call_args.args[1], 220)
        self.send.click.assert_called_once()
        self.ack.assert_called_once()
