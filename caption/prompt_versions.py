"""Versioned prompts stored in a prompt-specific directory.

Each version is a plain text file named ``v<N> - <name>.txt``. Saving an edit never
overwrites an existing version; it creates the next ``v<N+1>``, so any earlier prompt
can be selected again later. Files can also be added or edited by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import PROMPTS_DIR

SUMMARY_DIR = PROMPTS_DIR / "summarize"
CHAT_DIR = PROMPTS_DIR / "chat"
SUMMARY_PLACEHOLDERS = ("{file_name}", "{source}", "{started}", "{now}")
CHAT_PLACEHOLDERS = ("{message}", "{file_name}", "{source}", "{started}", "{now}")
PLACEHOLDERS = SUMMARY_PLACEHOLDERS

_VERSION = re.compile(r"^v(\d+)\b", re.IGNORECASE)


def _number(path: Path) -> int:
    m = _VERSION.match(path.stem)
    return int(m.group(1)) if m else 0


def list_versions(prompt_dir: Path = SUMMARY_DIR) -> list[str]:
    """Version names (file stems), oldest first."""
    if not prompt_dir.is_dir():
        return []
    files = sorted(prompt_dir.glob("*.txt"), key=lambda p: (_number(p), p.stem.lower()))
    return [p.stem for p in files]


def path_for(name: str, prompt_dir: Path = SUMMARY_DIR) -> Path:
    return prompt_dir / f"{name}.txt"


def resolve(name: str, prompt_dir: Path = SUMMARY_DIR) -> str | None:
    """``name`` if that version exists, else the newest version, else None."""
    versions = list_versions(prompt_dir)
    if name in versions:
        return name
    return versions[-1] if versions else None


def read(name: str, prompt_dir: Path = SUMMARY_DIR) -> str:
    return path_for(name, prompt_dir).read_text(encoding="utf-8")


def save_new_version(text: str, label: str, prompt_dir: Path = SUMMARY_DIR) -> str:
    """Store ``text`` as the next version; returns the new version name."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", label).strip(" .") or "untitled"
    number = max((_number(prompt_dir / f"{v}.txt") for v in list_versions(prompt_dir)), default=0) + 1
    name = f"v{number} - {label[:60].rstrip(' .')}"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path_for(name, prompt_dir).write_text(text.strip() + "\n", encoding="utf-8")
    return name
