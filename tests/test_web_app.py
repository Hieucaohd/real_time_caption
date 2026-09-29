from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from web_app import store
from web_app.service import Resampler


class StandaloneWebAppTests(unittest.TestCase):
    def test_web_store_and_transcript_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(store, "CONVERSATIONS_DIR", root / "conversations"):
                db = store.Store(root / "web.sqlite3")
                item = db.create("iPhone lesson")
                transcript = store.Transcript(item, db)
                transcript.begin()
                transcript.write("hello from iphone", True)

                self.assertIn("hello from iphone", transcript.read())
                self.assertEqual(db.list()[0].source, "Web microphone")

    def test_web_audio_resamples_to_16khz(self) -> None:
        samples = np.linspace(-1, 1, 48_000, dtype=np.float32)
        output = Resampler(48_000)(samples)
        self.assertTrue(15_990 <= len(output) <= 16_010)


if __name__ == "__main__":
    unittest.main()
