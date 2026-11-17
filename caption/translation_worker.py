"""Bounded translation lanes: two Chrome finals, one latest-only live preview."""

from __future__ import annotations

import queue
import threading
from collections import deque
from contextlib import nullcontext
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
    sync: bool = False

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
        self._active = {}
        self._closed = False
        self._paused = False
        # Only Chrome gets an extra final lane. UI-based translation and cloud
        # providers retain their existing serialized behavior/rate of requests.
        self._threads = [threading.Thread(target=self._run, args=(lane,),
                         name=f'transcript-translation-{lane}', daemon=True)
                         for lane in ('final', 'chrome-final', 'preview')]
        for thread in self._threads:
            thread.start()

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
        if config.engine == 'google_web':
            return self.sync_google(lines[0]['conversation_id'], config, force=force)
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
            self._condition.notify_all()
            return True

    def sync_google(self, conversation_id, config, *, force=False):
        """One in-flight snapshot and one latest pending snapshot per conversation."""
        config.validate()
        with self._condition:
            if self._closed:
                return False
            if self._paused and not force:
                raise TranslationError('Translation paused after an error. Enable Auto translate or Retry.')
            lines = self.store.lines(conversation_id)
            if not lines:
                return False
            text = '\n'.join(line['source_text'] for line in lines)
            if not force and self.store.google_source(conversation_id, config) == text:
                return False
            def matches(task):
                return task.sync and task.line['conversation_id'] == conversation_id and task.config == config
            for task in list(self._active.values()) + list(self._tasks):
                if matches(task) and task.line['source_text'] == text and not task.cancel.is_set():
                    return False
            self._tasks = deque(task for task in self._tasks if not matches(task))
            if len(self._tasks) >= self.capacity:
                raise TranslationError('Translation queue is full. Retry later.')
            self._paused = False
            task = Task({'conversation_id': conversation_id, 'source_text': text}, config,
                        lines=tuple(lines), sync=True)
            self._tasks.append(task)
            self._condition.notify_all()
            return True

    def preview(self, conversation_id, text, config, revision):
        config.validate()
        if config.engine != 'chrome':
            raise TranslationError('Live preview is only available with Chrome Translator API.')
        with self._condition:
            if not self._closed:
                if self._paused:
                    raise TranslationError("Translation paused after an error. Check settings and enable it again.")
                self._preview = Task({"conversation_id": conversation_id, "source_text": text}, config, preview=True, revision=revision)
                self._condition.notify_all()

    def cancel_preview(self):
        with self._condition:
            self._preview = None
            if task := self._active.get('preview'):
                task.cancel.set()

    def resume(self):
        with self._condition:
            self._paused = False

    def cancel_all(self):
        with self._condition:
            self.cancel_preview()
            for task in self._active.values():
                task.cancel.set()
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
            self._condition.notify_all()

    def join(self, timeout=None):
        """Wait for lane cleanup, including thread-owned Chrome connections."""
        import time
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0, deadline - time.monotonic()))

    def _eligible(self, lane):
        if lane == 'preview':
            return self._preview is not None
        return any(lane == 'final' or task.config.engine == 'chrome' for task in self._tasks)

    def _run(self, lane):
        session = getattr(self.translator, 'worker_session', nullcontext)
        with session():
            self._run_lane(lane)

    def _run_lane(self, lane):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._eligible(lane))
                if self._closed:
                    return
                if lane == 'preview':
                    task = self._preview
                    self._preview = None
                else:
                    index = next(i for i, candidate in enumerate(self._tasks)
                                 if lane == 'final' or candidate.config.engine == 'chrome')
                    task = self._tasks[index]
                    del self._tasks[index]
                self._active[lane] = task
            status, value = "done", ""
            try:
                if not task.preview:
                    for attempt in task.attempts:
                        self.store.finish(attempt, "working")
                text = "\n".join(line["source_text"] for line in task.members)
                if task.sync:
                    from .google_translate import translate
                    def latest():
                        with self._condition:
                            for candidate in list(self._tasks):
                                if (candidate.sync and candidate.config == task.config
                                        and candidate.line['conversation_id'] == task.line['conversation_id']):
                                    self._tasks.remove(candidate)
                                    task.line, task.lines = candidate.line, candidate.lines
                            return task.line['source_text']
                    def publish(raw):
                        if not task.cancel.is_set():
                            cid = task.line['conversation_id']
                            # Publish additions immediately, but advance the source
                            # checkpoint only when the final read succeeds.
                            self.store.append_google(cid, task.config, self.store.google_source(cid, task.config), raw)
                    value = translate(text, task.config.source, task.config.target, task.cancel,
                                      append=True, stream_key=task.line['conversation_id'] + ':' + task.config.fingerprint,
                                      latest=latest, on_result=publish)
                else:
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
                if task.sync and status == 'done':
                    value = self.store.append_google(task.line['conversation_id'], task.config,
                                                     task.line['source_text'], value)
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
                    self._active.pop(lane, None)
                    # An unavailable/reloading local app tab must not cancel
                    # unrelated lines or permanently disable realtime capture.
                    # Failed lines remain in SQLite for an explicit Retry.
                    if status == "error" and task.config.engine != 'chrome':
                        self._paused = True
                        self.cancel_all()
                self.results.put((task, status, value))
