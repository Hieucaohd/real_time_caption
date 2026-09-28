from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from caption import chatgpt, paths, prompt_versions


class PromptVersionTests(unittest.TestCase):
    def test_versions_can_be_managed_in_a_chat_prompt_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            prompt_dir = Path(folder)
            (prompt_dir / "v1 - default.txt").write_text("Question: {message}\n", encoding="utf-8")

            self.assertEqual(prompt_versions.resolve("", prompt_dir), "v1 - default")
            saved = prompt_versions.save_new_version("Please answer: {message}", "concise", prompt_dir)

            self.assertEqual(saved, "v2 - concise")
            self.assertEqual(prompt_versions.list_versions(prompt_dir), ["v1 - default", "v2 - concise"])
            self.assertIn("{message}", prompt_versions.read(saved, prompt_dir))

    def test_chat_prompt_is_filled_without_a_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            prompt = Path(folder) / "chat.txt"
            prompt.write_text("Instruction\n{message}\nFile: {file_name}", encoding="utf-8")

            result = chatgpt.build_prompt(None, "", None, prompt, "  What does this mean?  ")

            self.assertEqual(result, "Instruction\nWhat does this mean?\nFile:")

    def test_chat_prompt_can_include_transcript_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcript = root / "transcript.txt"
            prompt = root / "chat.txt"
            prompt.write_text("{file_name}|{source}|{started}|{message}", encoding="utf-8")

            result = chatgpt.build_prompt(
                transcript,
                "Windows Live Captions",
                datetime(2026, 9, 28, 10, 30, 0),
                prompt,
                "Explain",
            )

            self.assertEqual(result, "transcript.txt|Windows Live Captions|2026-09-28 10:30:00|Explain")

    def test_install_preserves_the_legacy_chat_prompt_as_v1(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = root / "bundle"
            data = root / "data"
            (bundle / "prompts" / "chat").mkdir(parents=True)
            (bundle / "prompts" / "chat" / "v1 - default.txt").write_text(
                "Bundled {message}", encoding="utf-8"
            )
            data.mkdir()
            (data / "chat_with_caption.txt").write_text("My old {message}", encoding="utf-8")

            with (
                patch.object(paths, "BUNDLE_DIR", bundle),
                patch.object(paths, "PROMPTS_DIR", data),
            ):
                paths.install_default_prompts()

            migrated = data / "chat" / "v1 - default.txt"
            self.assertEqual(migrated.read_text(encoding="utf-8"), "My old {message}")


if __name__ == "__main__":
    unittest.main()
