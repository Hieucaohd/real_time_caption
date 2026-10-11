from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from caption.vocab import NewWord
from caption.voca_queue import VocaSaveQueue


class VocaQueueTests(unittest.TestCase):
    def worker(self, save, capacity=100):
        worker = VocaSaveQueue(save, capacity)
        self.addCleanup(self.stop_worker, worker)
        return worker

    @staticmethod
    def stop_worker(worker):
        worker.close()
        worker._thread.join(3)

    def test_uploads_are_fifo_serial_and_snapshot_words_and_routing(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        main_thread = threading.get_ident()

        def save(key, collection, words, source):
            calls.append((key, collection, words[0].word, source, threading.get_ident()))
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            words[0].voca_status = "created"

        worker = self.worker(save)
        second = NewWord("second", translation="Original meaning")
        try:
            self.assertTrue(worker.submit([NewWord("first")], "key-1", "collection-1", "source-1", ("first",)))
            self.assertTrue(entered.wait(2))
            self.assertTrue(worker.submit([second], "key-2", "collection-2", "source-2", ("second",)))
            second.word = "changed"
            second.translation = "changed meaning"
            self.assertEqual(worker.pending, 2)
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
        results = [worker.results.get(timeout=2) for _ in range(4)]
        self.assertEqual([r[0] for r in results], ["saving", "done", "saving", "done"])
        self.assertEqual([c[:4] for c in calls], [
            ("key-1", "collection-1", "first", "source-1"),
            ("key-2", "collection-2", "second", "source-2"),
        ])
        self.assertTrue(all(c[-1] != main_thread for c in calls))
        self.assertEqual(results[-1][2][0].translation, "Original meaning")
        self.assertEqual(second.voca_status, "")
        self.assertEqual(worker.pending, 0)

    def test_failed_upload_does_not_kill_worker_or_leak_error_and_next_job_runs(self):
        save = Mock(side_effect=[RuntimeError("private api key"), None])
        worker = self.worker(save)
        with patch("caption.voca_queue.log.warning") as warning:
            worker.submit([NewWord("first")], "key", "", "", (1,))
            worker.submit([NewWord("second")], "key", "", "", (2,))
            results = [worker.results.get(timeout=2) for _ in range(4)]
        self.assertEqual(results[1][2][0].voca_status, "failed")
        self.assertNotIn("private", results[1][2][0].voca_error)
        self.assertNotIn("private", str(warning.call_args))
        self.assertEqual(save.call_count, 2)
        self.assertEqual(results[-1][1].message, (2,))
        self.assertEqual(worker.pending, 0)

    def test_full_queue_rejects_without_blocking_and_close_discards_unsent_jobs(self):
        entered, release = threading.Event(), threading.Event()

        def save(*args):
            entered.set()
            release.wait(2)

        save_mock = Mock(side_effect=save)
        worker = self.worker(save_mock, capacity=1)
        try:
            self.assertTrue(worker.submit([NewWord("first")], "key", "", "", (1,)))
            self.assertTrue(entered.wait(2))
            self.assertTrue(worker.submit([NewWord("second")], "key", "", "", (2,)))
            self.assertFalse(worker.submit([NewWord("third")], "key", "", "", (3,)))
            worker.close()
            self.assertFalse(worker.submit([NewWord("closed")], "key", "", "", (4,)))
            worker.results.get(timeout=2)  # First upload started.
            status, task, words = worker.results.get(timeout=2)
            self.assertEqual((status, task.message), ("done", (2,)))
            self.assertEqual(words[0].voca_status, "failed")
            self.assertIn("closed", words[0].voca_error)
        finally:
            release.set()
        worker.results.get(timeout=2)  # The already-sent first upload may finish.
        save_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
