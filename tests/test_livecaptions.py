from __future__ import annotations

import unittest
from unittest.mock import patch

from caption import livecaptions


class _TextControl:
    def __init__(self, text: str):
        self.Name = text


class LiveCaptionsReconnectTests(unittest.TestCase):
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
