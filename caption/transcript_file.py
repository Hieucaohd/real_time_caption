"""Auto-save of each caption session to ``transcripts/<source>_<start time>.txt``."""

from __future__ import annotations

import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path(__file__).resolve().parent.parent / "transcripts"
# Trimmed copies uploaded to ChatGPT (see TranscriptFile.snapshot); safe to delete any time.
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "real_time_caption"


def _safe_name(source: str) -> str:
    """Turn a source label into a short, filesystem-safe name."""
    source = re.sub(r"\s*\((default|read its text)\)", "", source)
    source = re.sub(r"[^\w\-]+", "-", source, flags=re.UNICODE).strip("-")
    return source[:60].rstrip("-") or "source"


class TranscriptFile:
    """Appends committed captions to a text file, flushing after every write so
    nothing is lost if the app is closed abruptly. Empty files are removed on close."""

    def __init__(self, source_label: str, folder: Path = TRANSCRIPTS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        self.source = source_label
        self.started = started
        self.path = folder / f"{_safe_name(source_label)}_{started:%Y-%m-%d_%H-%M-%S}.txt"
        self._file = open(self.path, "a", encoding="utf-8")
        self._file.write(f"# Source: {source_label}\n# Started: {started:%Y-%m-%d %H:%M:%S}\n\n")
        self._file.flush()
        self._line_open = False
        self._has_text = False

    @property
    def has_text(self) -> bool:
        return self._has_text

    def write(self, text: str, end_of_utterance: bool) -> None:
        if self._file.closed:
            return
        if not self._line_open:
            self._file.write(datetime.now().strftime("[%H:%M:%S] "))
        self._file.write(text + ("\n" if end_of_utterance else " "))
        self._file.flush()
        self._line_open = not end_of_utterance
        self._has_text = True

    def snapshot(self, max_lines: int) -> Path:
        """File to upload: the whole transcript, or a temporary copy with only the last
        ``max_lines`` caption lines (``max_lines <= 0`` means the whole file)."""
        if max_lines <= 0:
            return self.path
        lines = self.path.read_text(encoding="utf-8").splitlines()
        captions = [line for line in lines if line.startswith("[")]
        if len(captions) <= max_lines:
            return self.path
        header = [line for line in lines if line.startswith("#")]
        header.append(f"# Excerpt: the last {max_lines} of {len(captions)} caption lines")
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAPSHOT_DIR / f"{self.path.stem}_last{max_lines}.txt"
        path.write_text("\n".join(header + [""] + captions[-max_lines:]) + "\n", encoding="utf-8")
        return path

    def close(self) -> None:
        if self._file.closed:
            return
        if self._line_open:
            self._file.write("\n")
        self._file.close()
        if not self._has_text:
            try:
                self.path.unlink()
            except OSError:
                log.exception("Could not remove empty transcript %s", self.path)
