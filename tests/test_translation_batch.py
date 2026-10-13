from __future__ import annotations

import unittest
from unittest.mock import Mock

from caption.translation import Config, TranslationError
from caption.translation_batch import SentenceBatcher, batch_options


class Scheduler:
    def __init__(self):
        self.now = 0
        self.next_id = 0
        self.jobs = {}

    def after(self, delay, callback):
        self.next_id += 1
        self.jobs[self.next_id] = (self.now + delay, callback)
        return self.next_id

    def cancel(self, handle):
        self.jobs.pop(handle, None)

    def advance(self, milliseconds):
        end = self.now + milliseconds
        while self.jobs:
            handle, (due, callback) = min(self.jobs.items(), key=lambda item: item[1][0])
            if due > end:
                break
            self.now = due
            del self.jobs[handle]
            callback()
        self.now = end


class SentenceBatcherTests(unittest.TestCase):
    def setUp(self):
        self.clock = Scheduler()
        self.submit, self.error = Mock(), Mock()
        self.batcher = SentenceBatcher(self.clock.after, self.clock.cancel, self.submit, self.error)
        self.config = Config(key="test-key")

    def add(self, number, *, cid="one", config=None, count=3, timeout=5):
        line = {"id": number, "conversation_id": cid, "source_text": f"Sentence {number}."}
        self.batcher.add(line, config or self.config, count, timeout)
        return line

    def test_enough_lines_send_one_batch_and_cancel_timer(self):
        lines = [self.add(number) for number in range(3)]
        self.submit.assert_called_once_with(lines, self.config)
        self.assertEqual(self.clock.jobs, {})
        self.clock.advance(10000)
        self.submit.assert_called_once()
        self.assertEqual(self.batcher.lines, [])

    def test_timeout_is_from_first_line_not_last_and_next_group_gets_new_deadline(self):
        first = self.add(1)
        self.clock.advance(4000)
        second = self.add(2)
        self.clock.advance(999)
        self.submit.assert_not_called()
        self.clock.advance(1)
        self.submit.assert_called_once_with([first, second], self.config)
        third = self.add(3)
        self.clock.advance(4999)
        self.assertEqual(self.submit.call_count, 1)
        self.clock.advance(1)
        self.submit.assert_called_with([third], self.config)

    def test_count_one_preserves_immediate_translation(self):
        first = self.add(1, count=1)
        self.submit.assert_called_once_with([first], self.config)
        self.assertEqual(self.clock.jobs, {})

    def test_flush_before_pause_or_conversation_switch_uses_original_config(self):
        first = self.add(1)
        other = Config(engine="google", key="other-key")
        second = self.add(2, cid="two", config=other)
        self.submit.assert_called_once_with([first], self.config)
        self.batcher.flush()
        self.submit.assert_called_with([second], other)
        self.assertEqual(self.clock.jobs, {})

    def test_engine_credentials_or_limits_changes_do_not_mix_pending_groups(self):
        self.add(1)
        self.add(2, config=Config(key="changed-key"))
        self.add(3, count=4)
        self.add(4, timeout=10)
        self.assertEqual(self.submit.call_count, 3)
        self.assertTrue(all(len(call.args[0]) == 1 for call in self.submit.call_args_list))

    def test_cancel_discards_timer_and_keeps_no_automatic_send(self):
        self.add(1)
        self.batcher.clear()
        self.clock.advance(10000)
        self.submit.assert_not_called()
        self.assertIsNone(self.batcher.boundary)

    def test_duplicate_events_do_not_count_twice_and_input_is_snapshotted(self):
        first = self.add(1)
        self.batcher.add(first, self.config, 3, 5)
        first["source_text"] = "changed later"
        self.assertEqual(len(self.batcher.lines), 1)
        self.clock.advance(5000)
        self.assertEqual(self.submit.call_args.args[0][0]["source_text"], "Sentence 1.")

    def test_timeout_failure_is_reported_without_resending(self):
        self.submit.side_effect = TranslationError("queue full")
        self.add(1)
        self.clock.advance(5000)
        self.error.assert_called_once()
        self.clock.advance(10000)
        self.submit.assert_called_once()
        self.assertEqual(self.batcher.lines, [])

    def test_invalid_values_do_not_send_or_destroy_pending_lines(self):
        self.add(1)
        for count, timeout in ((0, 5), (51, 5), ("1.5", 5), ("", 5), (1, 0),
                               (1, -1), (1, 301), (1, "nan"), (1, "inf"), (1, "")):
            with self.subTest(count=count, timeout=timeout), self.assertRaises(TranslationError):
                self.batcher.add({"id": 2, "conversation_id": "one", "source_text": "B"}, self.config, count, timeout)
        self.assertEqual(len(self.batcher.lines), 1)
        self.assertEqual(batch_options(" 3 ", "0.5"), (3, 0.5))
        self.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
