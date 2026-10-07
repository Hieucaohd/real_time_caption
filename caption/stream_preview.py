"""Coalesce worker-thread response previews onto the Tk UI callback queue."""

from __future__ import annotations

import queue
import threading
from typing import Callable


class StreamPreview:
    def __init__(self, callbacks: queue.Queue, display: Callable[[str], None]):
        self._callbacks = callbacks
        self._display = display
        self._lock = threading.Lock()
        self._latest = ""
        self._queued = False
        self._closed = False

    def push(self, text: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._latest = text
            if self._queued:
                return
            self._queued = True
        self._callbacks.put(self._flush)

    def _flush(self) -> None:
        with self._lock:
            self._queued = False
            if self._closed:
                return
            text = self._latest
        self._display(text)

    def close(self) -> None:
        """Discard delayed previews so they cannot overwrite the final answer."""
        with self._lock:
            self._closed = True
