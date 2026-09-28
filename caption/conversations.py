"""Persistent conversations: SQLite is the index, folders hold the actual artifacts.

Each conversation owns a directory under ``transcripts/conversations``.  SQLite keeps
the searchable metadata and chat history; transcripts, summaries and screenshots stay
as ordinary files so they remain easy to inspect, back up and recover independently.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .paths import CONVERSATIONS_DB, CONVERSATIONS_DIR, TRANSCRIPTS_DIR

log = logging.getLogger(__name__)
PART_LIMIT = 5 * 1024 * 1024
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "real_time_caption" / "context"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    value = re.sub(r"\s*\((default|read its text)\)", "", value, flags=re.I)
    value = re.sub(r"[^\w\-]+", "-", value, flags=re.UNICODE).strip("-")
    return value[:70].rstrip("-") or "conversation"


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    source: str
    folder_name: str
    created_at: str
    updated_at: str
    last_started_at: str | None
    status: str

    @property
    def folder(self) -> Path:
        return CONVERSATIONS_DIR / self.folder_name

    @property
    def screenshots_dir(self) -> Path:
        return self.folder / "screenshots"


@dataclass(frozen=True)
class ChatMessage:
    id: int
    conversation_id: str
    role: str
    kind: str
    header: str
    content: str
    attachment: str | None
    created_at: str


class ConversationStore:
    def __init__(self, db_path: Path = CONVERSATIONS_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA journal_mode = WAL")
            yield db
            db.commit()
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    folder_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_started_at TEXT,
                    status TEXT NOT NULL DEFAULT 'paused',
                    legacy_path TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'chat',
                    header TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachment TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON chat_messages(conversation_id, id);
                """
            )
            # A previous process may have been killed while recording. Nothing is active
            # until this process explicitly starts a capture again.
            db.execute("UPDATE conversations SET status='paused' WHERE status='recording'")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> Conversation | None:
        if row is None:
            return None
        return Conversation(**{k: row[k] for k in Conversation.__dataclass_fields__})

    def list(self) -> list[Conversation]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [self._row(row) for row in rows if row is not None]  # type: ignore[misc]

    def get(self, conversation_id: str) -> Conversation | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return self._row(row)

    def create(self, source: str, title: str | None = None, created_at: datetime | None = None) -> Conversation:
        created = created_at or datetime.now()
        cid = uuid.uuid4().hex
        title = (title or source).strip() or "Conversation"
        folder_name = f"{_safe_name(title)}_{created:%Y-%m-%d_%H-%M-%S}_{cid[:8]}"
        folder = CONVERSATIONS_DIR / folder_name
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "screenshots").mkdir()
        stamp = created.isoformat(timespec="seconds")
        with self._connect() as db:
            db.execute(
                "INSERT INTO conversations(id,title,source,folder_name,created_at,updated_at,status) "
                "VALUES(?,?,?,?,?,?, 'paused')",
                (cid, title, source, folder_name, stamp, stamp),
            )
        self._write_manifest(cid)
        result = self.get(cid)
        assert result is not None
        return result

    def rename(self, conversation_id: str, title: str) -> Conversation:
        current = self.get(conversation_id)
        if current is None:
            raise KeyError(conversation_id)
        title = " ".join(title.split()).strip()
        if not title:
            raise ValueError("Conversation name cannot be empty.")
        created = _parse_time(current.created_at)
        new_name = f"{_safe_name(title)}_{created:%Y-%m-%d_%H-%M-%S}_{current.id[:8]}"
        if new_name != current.folder_name:
            target = CONVERSATIONS_DIR / new_name
            if target.exists():
                raise FileExistsError(target)
            current.folder.rename(target)
        now = _now()
        with self._connect() as db:
            db.execute(
                "UPDATE conversations SET title=?, folder_name=?, updated_at=? WHERE id=?",
                (title, new_name, now, conversation_id),
            )
        self._write_manifest(conversation_id)
        result = self.get(conversation_id)
        assert result is not None
        return result

    def set_status(self, conversation_id: str, status: str) -> None:
        now = _now()
        last_started = now if status == "recording" else None
        with self._connect() as db:
            if last_started:
                db.execute(
                    "UPDATE conversations SET status=?, updated_at=?, last_started_at=? WHERE id=?",
                    (status, now, last_started, conversation_id),
                )
            else:
                db.execute(
                    "UPDATE conversations SET status=?, updated_at=? WHERE id=?",
                    (status, now, conversation_id),
                )
        self._write_manifest(conversation_id)

    def touch(self, conversation_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))

    def add_message(
        self,
        conversation_id: str,
        role: str,
        header: str,
        content: str,
        *,
        kind: str = "chat",
        attachment: Path | None = None,
    ) -> None:
        created = _now()
        relative = None
        conversation = self.get(conversation_id)
        if attachment is not None and conversation is not None:
            try:
                relative = str(attachment.relative_to(conversation.folder))
            except ValueError:
                relative = str(attachment)
        with self._connect() as db:
            db.execute(
                "INSERT INTO chat_messages(conversation_id,role,kind,header,content,attachment,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (conversation_id, role, kind, header, content, relative, created),
            )
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created, conversation_id))
        self._write_chat_markdown(conversation_id)

    def messages(self, conversation_id: str) -> list[ChatMessage]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
            ).fetchall()
        return [ChatMessage(**dict(row)) for row in rows]

    def latest_summary(self, conversation_id: str) -> ChatMessage | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM chat_messages WHERE conversation_id=? AND kind='summary' "
                "AND role='bot' ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return ChatMessage(**dict(row)) if row else None

    def summaries(self, conversation_id: str) -> list[ChatMessage]:
        """All saved ChatGPT summary answers for a conversation, oldest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM chat_messages WHERE conversation_id=? AND kind='summary' "
                "AND role='bot' ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [ChatMessage(**dict(row)) for row in rows]

    def chat_context_path(self, conversation_id: str) -> Path | None:
        messages = self.messages(conversation_id)
        if not messages:
            return None
        self._write_chat_markdown(conversation_id)
        conversation = self.get(conversation_id)
        return conversation.folder / "chat_history.md" if conversation else None

    def _write_chat_markdown(self, conversation_id: str) -> None:
        conversation = self.get(conversation_id)
        if conversation is None:
            return
        lines = [f"# Chat history — {conversation.title}", ""]
        for message in self.messages(conversation_id):
            label = "You" if message.role == "user" else "ChatGPT"
            lines += [f"## {label} · {message.created_at}", "", message.content.strip(), ""]
        (conversation.folder / "chat_history.md").write_text("\n".join(lines), encoding="utf-8")

    def _write_manifest(self, conversation_id: str) -> None:
        conversation = self.get(conversation_id)
        if conversation is None:
            return
        data = {
            "id": conversation.id,
            "title": conversation.title,
            "source": conversation.source,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "last_started_at": conversation.last_started_at,
            "status": conversation.status,
        }
        conversation.folder.mkdir(parents=True, exist_ok=True)
        (conversation.folder / "conversation.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def import_legacy(self) -> int:
        """Non-destructively copy old top-level transcript files into conversations once."""
        imported = 0
        for old in sorted(TRANSCRIPTS_DIR.glob("*.txt")):
            try:
                with self._connect() as db:
                    exists = db.execute(
                        "SELECT 1 FROM conversations WHERE legacy_path=?", (str(old.resolve()),)
                    ).fetchone()
                if exists:
                    continue
                text = old.read_text(encoding="utf-8", errors="replace")
                source_match = re.search(r"^# Source:\s*(.+)$", text, re.M)
                started_match = re.search(r"^# Started:\s*(.+)$", text, re.M)
                source = source_match.group(1).strip() if source_match else old.stem.rsplit("_", 1)[0]
                try:
                    started = datetime.fromisoformat(started_match.group(1).strip()) if started_match else datetime.fromtimestamp(old.stat().st_mtime)
                except ValueError:
                    started = datetime.fromtimestamp(old.stat().st_mtime)
                conversation = self.create(source, source, started)
                shutil.copy2(old, conversation.folder / "transcript.txt")
                summary = old.with_name(old.stem + ".summary.md")
                if summary.exists():
                    shutil.copy2(summary, conversation.folder / "summaries.md")
                with self._connect() as db:
                    db.execute(
                        "UPDATE conversations SET legacy_path=?, updated_at=? WHERE id=?",
                        (str(old.resolve()), datetime.fromtimestamp(old.stat().st_mtime).isoformat(timespec="seconds"), conversation.id),
                    )
                self._write_manifest(conversation.id)
                imported += 1
            except OSError:
                log.exception("Could not import legacy transcript %s", old)
        return imported


class ConversationTranscript:
    """Append-only, optionally split transcript belonging to one conversation."""

    def __init__(self, conversation: Conversation, store: ConversationStore):
        self.conversation_id = conversation.id
        self.source = conversation.source
        self.started = _parse_time(conversation.created_at)
        self.folder = conversation.folder
        self.store = store
        self._line_open = False
        self._last_write_path: Path | None = None

    @property
    def path(self) -> Path:
        """Compatibility path used by vocabulary lookup; a full merged snapshot if split."""
        parts = self.parts()
        if len(parts) <= 1:
            return parts[0] if parts else self.folder / "transcript.txt"
        return self.snapshot(0)

    @property
    def has_text(self) -> bool:
        return any(any(line.startswith("[") for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()) for p in self.parts())

    def parts(self) -> list[Path]:
        first = self.folder / "transcript.txt"
        return ([first] if first.exists() else []) + sorted(self.folder.glob("transcript-[0-9][0-9][0-9][0-9].txt"))

    def begin_segment(self, source: str | None = None) -> None:
        path = self._write_path()
        with open(path, "a", encoding="utf-8") as f:
            if path.stat().st_size == 0:
                f.write(f"# Source: {self.source}\n# Started: {self.started:%Y-%m-%d %H:%M:%S}\n")
            label = f" · Source: {source}" if source and source != self.source else ""
            f.write(f"\n# Continued: {datetime.now():%Y-%m-%d %H:%M:%S}{label}\n\n")
        self._line_open = False

    def _write_path(self, extra_bytes: int = 0) -> Path:
        parts = self.parts()
        path = parts[-1] if parts else self.folder / "transcript.txt"
        if path.exists() and path.stat().st_size + extra_bytes >= PART_LIMIT:
            path = self.folder / f"transcript-{len(parts) + 1:04d}.txt"
        return path

    def write(self, text: str, end_of_utterance: bool) -> None:
        extra = len(text.encode("utf-8")) + 32
        path = self._write_path(extra)
        new_part = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            if new_part:
                f.write(f"# Source: {self.source}\n# Continued transcript part\n\n")
            if new_part or not self._line_open:
                f.write(datetime.now().strftime("[%Y-%m-%d %H:%M:%S] "))
            f.write(text + ("\n" if end_of_utterance else " "))
            f.flush()
        self._last_write_path = path
        self._line_open = not end_of_utterance

    def read_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace").rstrip() for p in self.parts()).strip()

    def snapshot(self, max_lines: int) -> Path:
        lines = self.read_text().splitlines()
        captions = [line for line in lines if line.startswith("[")]
        if max_lines > 0 and len(captions) > max_lines:
            headers = [line for line in lines if line.startswith("#")][:2]
            lines = headers + [f"# Excerpt: the last {max_lines} of {len(captions)} caption lines", ""] + captions[-max_lines:]
        elif len(self.parts()) == 1:
            return self.parts()[0]
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAPSHOT_DIR / f"{self.conversation_id}_transcript.txt"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path

    def close(self) -> None:
        if self._line_open:
            with open(self._last_write_path or self._write_path(), "a", encoding="utf-8") as f:
                f.write("\n")
            self._line_open = False
