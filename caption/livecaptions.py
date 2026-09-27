"""Read captions produced by the built-in Windows 11 Live Captions app.

Live Captions exposes its text through UI Automation (a TextBlock with
AutomationId ``CaptionsTextBlock``). That text is only a rolling window of the
last few lines and its newest sentences are still being revised, so we:

* split the text into sentences,
* treat the last ``IN_PROGRESS_UNITS`` sentences as a *partial* caption
  (all of them once the text has not changed for ``FLUSH_AFTER_S``),
* commit the rest as *final*, anchoring on the last committed sentence so that
  text scrolling off the top or being lightly revised is not duplicated.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .transcriber import Event

log = logging.getLogger(__name__)

LABEL = "Windows Live Captions (read its text)"
WINDOW_CLASS = "LiveCaptionsDesktopWindow"
TEXT_ID = "CaptionsTextBlock"
POLL_S = 0.2
FLUSH_AFTER_S = 1.5
IN_PROGRESS_UNITS = 2
LAUNCH_TIMEOUT_S = 15.0

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Unit:
    text: str
    ends_line: bool  # last sentence on its line: Live Captions breaks lines at pauses


def split_units(text: str) -> list[Unit]:
    units: list[Unit] = []
    for line in text.splitlines():
        parts = [p.strip() for p in _SENTENCE_END.split(line.strip()) if p.strip()]
        units.extend(Unit(p, i == len(parts) - 1) for i, p in enumerate(parts))
    return units


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def similar(a: str, b: str, truncated: bool = False) -> bool:
    """Fuzzy sentence equality. ``truncated``: ``a`` may be ``b`` with its start cut off."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return ka == kb
    if ka == kb or (truncated and kb.endswith(ka)):
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85


class CaptionDiffer:
    """Turns successive snapshots of the Live Captions text into partial/final captions."""

    def __init__(self) -> None:
        self.history: list[str] = []
        self._baseline_done = False

    def update(self, text: str, flush: bool) -> tuple[list[Unit], str]:
        """Return (units to commit, partial text) for the latest snapshot."""
        units = split_units(text)
        cut = len(units) if flush else max(0, len(units) - IN_PROGRESS_UNITS)

        if not self._baseline_done and len(units) > 1:
            # The oldest sentence on screen when we attach is usually cut off at the front;
            # mark it as already seen so we don't commit a fragment.
            self.history.append(units[0].text)
            self._baseline_done = True

        anchor = self._find_anchor(units)
        if anchor is None:
            # Anchor scrolled away or was heavily revised: fall back to fuzzy de-duplication.
            recent = self.history[-30:]
            new = [
                u for i, u in enumerate(units[:cut])
                if not any(similar(u.text, h, truncated=i == 0) for h in recent)
            ]
            start = cut
        else:
            start = anchor + 1
            new = units[start:cut]

        self.history.extend(u.text for u in new)
        del self.history[:-200]
        partial = " ".join(u.text for u in units[max(start, cut) :])
        return new, partial

    def _find_anchor(self, units: list[Unit]) -> int | None:
        """Index of the most recently committed sentence within ``units``, if visible."""
        if not self.history:
            return None
        last = self.history[-1]
        prev = self.history[-2] if len(self.history) > 1 else None
        for i in range(len(units) - 1, -1, -1):
            if not similar(units[i].text, last, truncated=i == 0):
                continue
            if prev is None or i == 0 or similar(units[i - 1].text, prev, truncated=i - 1 == 0):
                return i
        return None


class LiveCaptionsReader:
    """Polls the Live Captions window on a background thread and emits :class:`Event` s."""

    def __init__(self, on_event: Callable[[Event], None]):
        self._emit = on_event
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="livecaptions", daemon=True)
        self.level = 0.0  # no audio meter for this source

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import uiautomation as auto

        try:
            with auto.UIAutomationInitializerInThread():
                self._loop(auto)
        except Exception as exc:  # noqa: BLE001
            log.exception("Live Captions reader crashed")
            self._emit(Event("error", f"Live Captions reader failed:\n{exc}"))
        self._emit(Event("stopped"))

    def _find_text(self, auto, launch: bool):
        window = auto.WindowControl(searchDepth=1, ClassName=WINDOW_CLASS)
        if not window.Exists(0.5):
            if not launch:
                return None
            self._emit(Event("status", "Starting Windows Live Captions…"))
            exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "LiveCaptions.exe")
            subprocess.Popen([exe])
            deadline = time.monotonic() + LAUNCH_TIMEOUT_S
            while not window.Exists(0.5):
                if self._stop.is_set() or time.monotonic() > deadline:
                    return None
        text = window.TextControl(AutomationId=TEXT_ID)
        return text if text.Exists(3) else None

    def _loop(self, auto) -> None:
        text_ctrl = self._find_text(auto, launch=True)
        if text_ctrl is None:
            if not self._stop.is_set():
                self._emit(
                    Event(
                        "error",
                        "Could not find the Windows Live Captions window.\n"
                        "Open it with Win + Ctrl + L (Windows 11 22H2 or newer) and try again.",
                    )
                )
            return

        self._emit(Event("ready", "Reading Windows Live Captions — keep its window open (it can sit behind others)"))
        differ = CaptionDiffer()
        last_text: str | None = None
        last_change = time.monotonic()
        flushed = False

        while not self._stop.wait(POLL_S):
            try:
                text = text_ctrl.Name or ""
            except Exception:  # noqa: BLE001 - window closed or recreated
                text_ctrl = self._find_text(auto, launch=False)
                if text_ctrl is None:
                    self._emit(Event("status", "Live Captions window closed — waiting for it to reopen…"))
                    self._stop.wait(1.0)
                continue

            now = time.monotonic()
            if text != last_text:
                last_text, last_change, flushed = text, now, False
            elif flushed or now - last_change < FLUSH_AFTER_S:
                continue

            flush = now - last_change >= FLUSH_AFTER_S
            new, partial = differ.update(text, flush=flush)
            flushed = flush
            for i, unit in enumerate(new):
                is_last = i == len(new) - 1
                self._emit(Event("final", unit.text, end_of_utterance=unit.ends_line or (flush and is_last)))
            self._emit(Event("partial", partial))
