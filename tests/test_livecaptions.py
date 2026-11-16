from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from caption import livecaptions


class _TextControl:
    def __init__(self, text: str):
        self.Name = text


class UnicodeCaptionTests(unittest.TestCase):
    def test_distinct_japanese_chinese_and_accented_words_are_not_duplicates(self):
        for a, b in [('今日は晴れです。', '明日は雨になります。'), ('你好世界', '今天学习数学'), ('má', 'mà')]:
            self.assertFalse(livecaptions.similar(a, b), (a, b))
        self.assertTrue(livecaptions.similar('Hello, WORLD!', 'hello world'))
        self.assertTrue(livecaptions.similar('café', 'cafe\u0301'))

    def test_japanese_rolling_captions_emit_new_lines_once(self):
        differ = livecaptions.CaptionDiffer()
        lines = ['今日は晴れです。', '数学について勉強しましょう。', '新しい言葉を覚えます。', '明日は雨になります。']
        finals = []
        for index in range(len(lines)):
            snapshot = '\n'.join(lines[max(0, index - 1):index + 1])
            new, partial = differ.update(snapshot, flush=True)
            finals.extend(unit.text for unit in new)
            self.assertEqual(partial, '')
            self.assertEqual(differ.update(snapshot, flush=True)[0], [])
        self.assertEqual(finals, lines)

    def test_scroll_container_supplies_text_when_leaf_empty_or_stale(self):
        scroll = _TextControl('新しい文章です。')
        self.assertEqual(livecaptions._CaptionText(_TextControl(''), scroll).Name, scroll.Name)
        class Stale:
            @property
            def Name(self): raise RuntimeError('Element no longer exists')
        self.assertEqual(livecaptions._CaptionText(Stale(), scroll).Name, scroll.Name)
        self.assertEqual(livecaptions._CaptionText(scroll, Stale()).Name, scroll.Name)
        self.assertEqual(livecaptions._CaptionText(Stale(), Stale()).Name, '')


class LiveCaptionsReconnectTests(unittest.TestCase):
    def test_com_failure_during_refresh_recovers_without_losing_differ(self):
        COMError = type('COMError', (Exception,), {})
        reader = livecaptions.LiveCaptionsReader(lambda event: None)
        events = []
        def emit(event):
            events.append(event)
            if event.kind == 'final': reader.stop()
        reader._emit = emit
        with (patch.object(reader, '_find_text_control', side_effect=[COMError(), _TextControl('Recovered caption.')]),
              patch.object(reader, '_window_exists', return_value=True),
              patch.object(livecaptions, 'POLL_S', .001), patch.object(livecaptions, 'FLUSH_AFTER_S', .002)):
            reader._loop(object())
        self.assertEqual([e.text for e in events if e.kind == 'final'], ['Recovered caption.'])
        self.assertFalse(any(e.kind == 'error' for e in events))
        auto = Mock()
        auto.WindowControl.return_value.Exists.side_effect = COMError()
        self.assertTrue(reader._window_exists(auto))

    def test_reader_only_reads_captions_without_settings_callback(self):
        events = []
        reader = livecaptions.LiveCaptionsReader(events.append)
        self.assertFalse(hasattr(reader, 'configure'))
        def emit(event):
            events.append(event)
            if event.kind == 'final': reader.stop()
        reader._emit = emit
        with (patch.object(reader, '_find_text', return_value=_TextControl('字幕は引き続き表示されます。')),
              patch.object(livecaptions, 'POLL_S', .001), patch.object(livecaptions, 'FLUSH_AFTER_S', .002)):
            reader._loop(object())
        self.assertFalse(any(e.kind == 'warning' for e in events))
        self.assertFalse(any(e.kind == 'error' for e in events))
        self.assertEqual([e.text for e in events if e.kind == 'final'], ['字幕は引き続き表示されます。'])
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
