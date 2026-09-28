"""Where the app's files live, both when run from source and as a packaged .exe.

* ``BUNDLE_DIR``: read-only files shipped with the app (default prompts, icon). From
  source this is the project folder; in a PyInstaller build it is ``_internal``.
* ``DATA_DIR``: everything the app writes or the user edits (settings, prompts,
  transcripts, downloaded models, log). From source this is the project folder; in a
  build it is the folder next to ``RealTimeCaption.exe`` (so the whole folder is
  portable), or ``%LOCALAPPDATA%\\RealTimeCaption`` if that folder isn't writable.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def _data_dir() -> Path:
    if not FROZEN:
        return BUNDLE_DIR
    exe_dir = Path(sys.executable).resolve().parent
    if _writable(exe_dir):
        return exe_dir
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RealTimeCaption"


DATA_DIR = _data_dir()
PROMPTS_DIR = DATA_DIR / "prompts"
MODELS_DIR = DATA_DIR / "models"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "caption.log"
CACHE_DIR = DATA_DIR / "cache"  # e.g. matplotlib's font cache, so it isn't rebuilt on every start
ICON_PATH = BUNDLE_DIR / "assets" / "icon.ico"


def install_default_prompts() -> None:
    """Copy the bundled prompts next to the .exe on first run, without touching edited ones."""
    source = BUNDLE_DIR / "prompts"
    if source.resolve() == PROMPTS_DIR.resolve() or not source.is_dir():
        return
    for src in source.rglob("*.txt"):
        dst = PROMPTS_DIR / src.relative_to(source)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
