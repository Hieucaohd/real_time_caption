"""Versioned summary prompts in ``prompts/summarize/``.

Each version is a plain text file named ``v<N> - <name>.txt``. Saving an edit never
overwrites an existing version; it creates the next ``v<N+1>``, so any earlier prompt
can be selected again later. Files can also be added or edited by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

SUMMARY_DIR = Path(__file__).resolve().parent.parent / "prompts" / "summarize"
PLACEHOLDERS = ("{file_name}", "{source}", "{started}", "{now}")

_VERSION = re.compile(r"^v(\d+)\b", re.IGNORECASE)


def _number(path: Path) -> int:
    m = _VERSION.match(path.stem)
    return int(m.group(1)) if m else 0


def list_versions() -> list[str]:
    """Version names (file stems), oldest first."""
    if not SUMMARY_DIR.is_dir():
        return []
    files = sorted(SUMMARY_DIR.glob("*.txt"), key=lambda p: (_number(p), p.stem.lower()))
    return [p.stem for p in files]


def path_for(name: str) -> Path:
    return SUMMARY_DIR / f"{name}.txt"


def resolve(name: str) -> str | None:
    """``name`` if that version exists, else the newest version, else None."""
    versions = list_versions()
    if name in versions:
        return name
    return versions[-1] if versions else None


def read(name: str) -> str:
    return path_for(name).read_text(encoding="utf-8")


def save_new_version(text: str, label: str) -> str:
    """Store ``text`` as the next version; returns the new version name."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", label).strip(" .") or "untitled"
    number = max((_number(SUMMARY_DIR / f"{v}.txt") for v in list_versions()), default=0) + 1
    name = f"v{number} - {label[:60].rstrip(' .')}"
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path_for(name).write_text(text.strip() + "\n", encoding="utf-8")
    return name
