"""A bounded FIFO for Voca uploads, independent of ChatGPT and Tk widgets."""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Callable

from .vocab import NewWord

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VocaSaveTask:
    words: tuple[NewWord, ...]
    api_key: str = field(repr=False)
    collection_id: str
    source_title: str
    message: tuple = field(repr=False)


class VocaSaveQueue:
    def __init__(self, save: Callable, capacity: int = 100):
        self._save = save
        self.capacity = capacity
        self.results = queue.Queue()
        self._condition = threading.Condition()
        self._tasks = deque()
        self._active = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="voca-save-queue", daemon=True)
        self._thread.start()

    @property
    def pending(self) -> int:
        with self._condition:
            return len(self._tasks) + int(self._active is not None)

    def submit(self, words, api_key, collection_id, source_title, message) -> bool:
        # Freeze routing and copy word cards before the caller can change settings
        # or another ChatGPT request can mutate its own word list.
        task = VocaSaveTask(
            tuple(replace(word, voca_status="", voca_error="") for word in words),
            api_key, collection_id, source_title, message,
        )
        with self._condition:
            if self._closed or len(self._tasks) >= self.capacity:
                return False
            self._tasks.append(task)
            self._condition.notify()
            return True

    @staticmethod
    def failed_words(task, error):
        return [replace(word, voca_status="failed", voca_error=error) for word in task.words]

    def close(self) -> None:
        """Discard unsent tasks; an HTTP request already sent may still finish."""
        with self._condition:
            self._closed = True
            while self._tasks:
                task = self._tasks.popleft()
                self.results.put(("done", task, self.failed_words(task, "App closed before saving to Voca.")))
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._tasks)
                if self._closed:
                    return
                task = self._tasks.popleft()
                self._active = task
            self.results.put(("saving", task, task.words))
            words = [replace(word) for word in task.words]
            try:
                self._save(task.api_key, task.collection_id, words, task.source_title)
            except Exception as error:
                # Third-party exception text can contain credentials or payloads.
                log.warning("Background Voca upload failed (%s)", type(error).__name__)
                words = self.failed_words(task, "Could not save to Voca. Please try again.")
            finally:
                with self._condition:
                    self._active = None
            self.results.put(("done", task, words))
