"""Bounded serial worker with immutable request configs and coalesced previews."""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass, field

from .translation import Config, TranslationError, Translator


@dataclass
class Task:
    line: dict
    config: Config
    attempt: int | None = None
    preview: bool = False
    revision: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    lines: tuple = ()
    attempts: tuple = ()

    @property
    def members(self):
        return self.lines or (self.line,)


class TranslationWorker:
    def __init__(self, store, translator=None, capacity=256):
        self.store = store
        self.translator = translator or Translator()
        self.capacity = capacity
        self.results = queue.Queue()
        self._condition = threading.Condition()
        self._tasks = deque()
        self._keys = set()
        self._preview = None
        self._active = None
        self._closed = False
        self._paused = False
        self._thread = threading.Thread(target=self._run, name="transcript-translation", daemon=True)
        self._thread.start()

    @staticmethod
    def _key(line, config):
        return line["id"], line["source_text"], config.fingerprint

    def submit(self, line, config, *, force=False):
        return self.submit_batch([line], config, force=force)

    def submit_batch(self, lines, config, *, force=False):
        config.validate()
        if not lines:
            return False
        if len({line["conversation_id"] for line in lines}) != 1:
            raise TranslationError("Cannot batch lines from different conversations.")
        with self._condition:
            if self._closed:
                return False
            if self._paused:
                if not force:
                    raise TranslationError("Translation paused after an error. Retry a line or enable Auto translate again.")
                self._paused = False
            pending, seen = [], set()
            for line in lines:
                key = self._key(line, config)
                if key in seen or key in self._keys:
                    continue
                seen.add(key)
                if force or not self.store.has_success(line, config):
                    pending.append(dict(line))
            if not pending:
                return False
            if len(self._tasks) >= self.capacity:
                raise TranslationError("Translation queue is full. Untranslated lines are saved; use Translate missing later.")
            attempts = tuple(self.store.begin_batch(pending, config))
            task = Task(pending[0], config, attempts[0], lines=tuple(pending), attempts=attempts)
            self._tasks.append(task)
            self._keys.update(self._key(line, config) for line in pending)
            self._condition.notify()
            return True

    def preview(self, conversation_id, text, config, revision):
        config.validate()
        with self._condition:
            if not self._closed:
                if self._paused:
                    raise TranslationError("Translation paused after an error. Check settings and enable it again.")
                self._preview = Task({"conversation_id": conversation_id, "source_text": text}, config, preview=True, revision=revision)
                self._condition.notify()

    def cancel_preview(self):
        with self._condition:
            self._preview = None
            if self._active and self._active.preview:
                self._active.cancel.set()

    def resume(self):
        with self._condition:
            self._paused = False

    def cancel_all(self):
        with self._condition:
            self.cancel_preview()
            if self._active:
                self._active.cancel.set()
            while self._tasks:
                task = self._tasks.popleft()
                try:
                    for attempt in task.attempts:
                        self.store.finish(attempt, "cancelled", error="Cancelled before sending.")
                except Exception:
                    pass  # Startup recovery will mark interrupted rows; keep cancelling safely.
                for line in task.members:
                    self._keys.discard(self._key(line, task.config))
                self.results.put((task, "cancelled", ""))

    def close(self):
        with self._condition:
            self._closed = True
            self.cancel_all()
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._tasks or self._preview)
                if self._closed:
                    return
                task = self._tasks.popleft() if self._tasks else self._preview
                if task is self._preview:
                    self._preview = None
                self._active = task
            status, value = "done", ""
            try:
                if not task.preview:
                    for attempt in task.attempts:
                        self.store.finish(attempt, "working")
                text = "\n".join(line["source_text"] for line in task.members)
                value = self.translator.translate(text, task.config, task.cancel)
            except Exception as error:
                # Do not log exception bodies: third-party errors can contain keys.
                status, value = "error", "Translation failed. Check settings, connection or offline dependencies."
                # Known errors contain only messages generated by our own adapter.
                if isinstance(error, TranslationError):
                    value = str(error)
            if task.cancel.is_set():
                status, value = "cancelled", "Cancelled; the provider may already have received this request."
            try:
                if not task.preview:
                    for attempt in task.attempts:
                        self.store.finish(attempt, status, value if status == "done" else "", value if status != "done" else "")
            except Exception:
                status, value = "error", "Could not save translation history. Retry after checking SQLite access."
            finally:
                with self._condition:
                    if not task.preview:
                        for line in task.members:
                            self._keys.discard(self._key(line, task.config))
                    self._active = None
                    if status == "error":
                        self._paused = True
                        self.cancel_all()
                self.results.put((task, status, value))
