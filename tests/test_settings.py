from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from caption import settings
from caption.gui import App


class SettingsMigrationTests(unittest.TestCase):
    def test_global_chatgpt_values_migrate_to_independent_tab_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "chatgpt_power": 2,
                        "chatgpt_max_lines": 50,
                        "chatgpt_new_chat": False,
                        "chatgpt_selected_tab": "legacy-selected-tab",
                    }
                ),
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
        self.assertEqual(loaded.summary_chatgpt_selected_tab, "legacy-selected-tab")

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

    def test_save_settings_collects_values_from_every_panel(self) -> None:
        app = object.__new__(App)
        app.settings = settings.Settings()
        app._summary_max_lines = Mock(return_value=25)
        app._save_summary_power = Mock()
        app.new_chat_var = SimpleNamespace(get=lambda: False)
        app.summary_attach_transcript_var = SimpleNamespace(get=lambda: False)
        app.chat = SimpleNamespace(persist_settings=Mock())
        app.vocab = SimpleNamespace(persist_settings=Mock())
        app.overlay_var = SimpleNamespace(get=lambda: False)
        app.model_var = SimpleNamespace(get=lambda: "medium.en")
        app.compute_var = SimpleNamespace(get=lambda: "cpu")
        app.device_var = SimpleNamespace(get=lambda: "Microphone")
        app.status_var = SimpleNamespace(set=Mock())

        with patch.object(app.settings, "save") as save:
            app.save_settings()

        self.assertFalse(app.settings.summary_new_chat)
        self.assertFalse(app.settings.summary_attach_transcript)
        self.assertFalse(app.settings.show_overlay)
        self.assertEqual(app.settings.model, "medium.en")
        self.assertEqual(app.settings.compute, "cpu")
        self.assertEqual(app.settings.device_label, "Microphone")
        app.chat.persist_settings.assert_called_once_with()
        app.vocab.persist_settings.assert_called_once_with()
        save.assert_called_once_with()
        app.status_var.set.assert_called_once_with("Settings saved")


if __name__ == "__main__":
    unittest.main()
