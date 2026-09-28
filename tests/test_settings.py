from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caption import settings


class SettingsMigrationTests(unittest.TestCase):
    def test_global_chatgpt_values_migrate_to_independent_tab_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps({"chatgpt_power": 2, "chatgpt_max_lines": 50, "chatgpt_new_chat": False}),
                encoding="utf-8",
            )
            with patch.object(settings, "SETTINGS_PATH", path):
                loaded = settings.Settings.load()

        self.assertEqual(loaded.summary_chatgpt_power, 2)
        self.assertEqual(loaded.chat_chatgpt_power, 2)
        self.assertEqual(loaded.vocab_chatgpt_power, 2)
        self.assertEqual(loaded.summary_max_lines, 50)
        self.assertEqual(loaded.chat_max_lines, 50)
        self.assertFalse(loaded.summary_new_chat)
        self.assertFalse(loaded.chat_attach_history)

    def test_explicit_tab_values_are_not_overwritten_by_legacy_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "chatgpt_power": 2,
                        "summary_chatgpt_power": 0,
                        "chat_chatgpt_power": 1,
                        "vocab_chatgpt_power": -1,
                        "summary_max_lines": 100,
                        "chat_max_lines": 25,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(settings, "SETTINGS_PATH", path):
                loaded = settings.Settings.load()

        self.assertEqual(
            (loaded.summary_chatgpt_power, loaded.chat_chatgpt_power, loaded.vocab_chatgpt_power),
            (0, 1, -1),
        )
        self.assertEqual((loaded.summary_max_lines, loaded.chat_max_lines), (100, 25))


if __name__ == "__main__":
    unittest.main()
