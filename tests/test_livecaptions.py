from __future__ import annotations

import unittest
from unittest.mock import patch

from caption import livecaptions


class _TextControl:
    def __init__(self, text: str):
        self.Name = text


class LiveCaptionsReconnectTests(unittest.TestCase):
    def test_ready_window_without_text_waits_for_first_caption(self) -> None:
        events = []
        reader = livecaptions.LiveCaptionsReader(events.append)
        working = _TextControl("First spoken caption.")

        def emit(event):
            events.append(event)
            if event.kind == "final":
                reader.stop()

        reader._emit = emit
        with (
            patch.object(reader, "_find_text", side_effect=[None, None, working]),
            patch.object(reader, "_window_exists", return_value=True),
            patch.object(livecaptions, "POLL_S", 0.001),
            patch.object(livecaptions, "FLUSH_AFTER_S", 0.002),
            patch.object(reader._stop, "wait", side_effect=lambda timeout: reader._stop.is_set()),
        ):
            reader._loop(object())

        self.assertFalse(any(event.kind == "error" for event in events))
        self.assertTrue(any(event.kind == "ready" and "Waiting" in event.text for event in events))
        self.assertFalse(any("window closed" in event.text for event in events))
        self.assertEqual([event.text for event in events if event.kind == "final"], ["First spoken caption."])

    def test_missing_window_reports_error(self) -> None:
        events = []
        reader = livecaptions.LiveCaptionsReader(events.append)
        with (
            patch.object(reader, "_find_text", return_value=None),
            patch.object(reader, "_window_exists", return_value=False),
        ):
            reader._loop(object())
        self.assertEqual([event.kind for event in events], ["error"])

    def test_stop_while_waiting_for_first_caption(self) -> None:
        events = []
        reader = livecaptions.LiveCaptionsReader(events.append)

        def emit(event):
            events.append(event)
            if event.kind == "ready":
                reader.stop()

        reader._emit = emit
        with (
            patch.object(reader, "_find_text", return_value=None) as find_text,
            patch.object(reader, "_window_exists", return_value=True),
        ):
            reader._loop(object())
        find_text.assert_called_once()
        self.assertEqual([event.kind for event in events], ["ready"])

    def test_empty_text_control_is_reacquired(self) -> None:
        events = []
        reader = livecaptions.LiveCaptionsReader(events.append)
        empty = _TextControl("")
        working = _TextControl("Fresh caption after Continue.")

        original_emit = reader._emit

        def emit(event):
            original_emit(event)
            if event.kind == "final":
                reader._stop.set()

        reader._emit = emit
        with (
            patch.object(reader, "_find_text", side_effect=[empty, working]),
            patch.object(livecaptions, "POLL_S", 0.001),
            patch.object(livecaptions, "EMPTY_REATTACH_S", 0.003),
            patch.object(livecaptions, "STALE_REATTACH_S", 1.0),
            patch.object(livecaptions, "FLUSH_AFTER_S", 0.002),
        ):
            reader._loop(object())

        finals = [event.text for event in events if event.kind == "final"]
        self.assertEqual(finals, ["Fresh caption after Continue."])


if __name__ == "__main__":
    unittest.main()
