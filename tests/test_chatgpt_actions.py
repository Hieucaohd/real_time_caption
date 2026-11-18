from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from caption import chatgpt, chatgpt_actions, paths


class ChatGPTActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        cache = patch.object(paths, 'CACHE_DIR', self.cache)
        cache.start()
        self.addCleanup(cache.stop)

    def test_ten_seconds_from_finish_including_failed_actions(self):
        now = 100.0
        def advance(seconds):
            nonlocal now
            now += seconds

        with patch.object(time, 'time', lambda: now), patch.object(time, 'monotonic', lambda: now), \
             patch.object(time, 'sleep', advance):
            with self.assertRaisesRegex(ValueError, 'operation failed'):
                with chatgpt_actions.action():
                    self.assertEqual(now, 100)
                    advance(7)
                    raise ValueError('operation failed')
            with chatgpt_actions.action():
                self.assertEqual(now, 117)

    def test_cancel_in_queue_does_not_delay_the_next_action(self):
        cancel = threading.Event()
        def cancel_on_wait(status):
            cancel.set()

        path = self.cache / 'chatgpt-actions.sqlite3'
        with chatgpt_actions.action():
            with self.assertRaises(chatgpt.ChatGPTCancelled):
                with chatgpt_actions.action(cancel, cancel_on_wait):
                    self.fail('Cancelled action was executed')
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM queue').fetchone()[0], 1)
                self.assertIsNone(db.execute('SELECT finished_at FROM last_action').fetchone())

    def test_fifo_across_processes_and_no_overlap(self):
        program = '''import sys,time
from pathlib import Path
from caption import paths,chatgpt_actions as actions
paths.CACHE_DIR = Path(sys.argv[1])
actions.COOLDOWN = .25
with actions.action(on_status=lambda status: print('waiting', flush=True)):
    print(time.time(), flush=True)
    time.sleep(.15)
    print(time.time(), flush=True)
'''
        workers = []
        def start():
            worker = subprocess.Popen([sys.executable, '-c', program, str(self.cache)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            workers.append(worker)
            return worker

        def wait_for_size(size):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with closing(sqlite3.connect(self.cache / 'chatgpt-actions.sqlite3')) as db:
                    if db.execute('SELECT count(*) FROM queue').fetchone()[0] == size:
                        return
                time.sleep(.02)
            self.fail('Worker did not enter the shared queue')

        try:
            with chatgpt_actions.action():
                first = start()
                wait_for_size(2)
                second = start()
                wait_for_size(3)
            finished = time.time()
            limits = []
            for worker in (first, second):
                out, err = worker.communicate(timeout=8)
                self.assertEqual(worker.returncode, 0, err)
                limits.append([float(line) for line in out.splitlines() if line != 'waiting'])
            self.assertGreaterEqual(limits[0][0] - finished, .23)
            self.assertGreaterEqual(limits[1][0] - limits[0][1], .24)
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate(timeout=5)

    def test_dead_worker_releases_queue_and_preserves_cooldown(self):
        program = '''import os,sys
from pathlib import Path
from caption import paths,chatgpt_actions as actions
paths.CACHE_DIR = Path(sys.argv[1])
with actions.action():
    os._exit(0)
'''
        subprocess.run([sys.executable, '-c', program, str(self.cache)], timeout=5, check=True)
        start = time.monotonic()
        with patch.object(chatgpt_actions, 'COOLDOWN', .2), chatgpt_actions.action():
            self.assertGreaterEqual(time.monotonic() - start, .19)
