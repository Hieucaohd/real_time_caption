"""Main-thread sentence batching with a deadline anchored to the first line."""

from __future__ import annotations

import math

from .translation import TranslationError


def batch_options(count, timeout):
    try:
        count = int(str(count).strip())
        timeout = float(timeout)
        if not 1 <= count <= 50 or not math.isfinite(timeout) or not 0.1 <= timeout <= 300:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise TranslationError("Batch size must be 1–50 lines; timeout must be 0.1–300 seconds.") from None
    return count, timeout


class SentenceBatcher:
    def __init__(self, schedule, cancel_timer, submit, on_error):
        self.schedule, self.cancel_timer = schedule, cancel_timer
        self.submit, self.on_error = submit, on_error
        self.lines = []
        self.timer = None
        self.boundary = None

    def add(self, line, config, count, timeout):
        count, timeout = batch_options(count, timeout)
        boundary = (line["conversation_id"], config, count, timeout)
        if self.lines and boundary != self.boundary:
            self.flush()
        if any(item["id"] == line["id"] and item["source_text"] == line["source_text"] for item in self.lines):
            return
        self.boundary = boundary
        self.lines.append(dict(line))
        if len(self.lines) >= count:
            self.flush()
        elif self.timer is None:
            self.timer = self.schedule(max(1, round(timeout * 1000)), self._timeout)

    def clear(self):
        if self.timer is not None:
            self.cancel_timer(self.timer)
        self.lines, self.timer, self.boundary = [], None, None

    def flush(self):
        if not self.lines:
            return
        lines, config = self.lines, self.boundary[1]
        self.clear()
        self.submit(lines, config)

    def _timeout(self):
        self.timer = None
        try:
            self.flush()
        except Exception as exc:
            self.on_error(exc)
