"""Local, immutable copies of explicitly selected chat attachments."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def snapshot_files(folder: Path, sources, existing_names=()) -> tuple[Path, ...]:
    # Do not recreate a conversation folder if it was renamed while importing.
    parent = folder / "attachments"
    parent.mkdir(exist_ok=True)
    batch = parent / uuid.uuid4().hex
    batch.mkdir()
    used = {name.casefold() for name in (*existing_names, "transcript.txt", "chat_history.md")}
    created = []
    try:
        for source in sources:
            source = Path(source)
            if not source.is_file():
                raise OSError(f"File is unavailable: {source.name}")
            name, number = source.name, 2
            while name.casefold() in used:
                name = f"{source.stem} ({number}){source.suffix}"
                number += 1
            used.add(name.casefold())
            target = batch / name
            temporary = batch / f".importing-{uuid.uuid4().hex}"
            created.append(temporary)
            shutil.copy2(source, temporary)
            temporary.rename(target)
            created.append(target)
        return tuple(path for path in created if path.exists())
    except Exception:
        # Only remove files created by this import, never any external originals.
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            batch.rmdir()
        except OSError:
            pass
        raise
