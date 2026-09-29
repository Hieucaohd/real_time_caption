from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "web_data"
CONVERSATIONS_DIR = DATA_DIR / "conversations"
DB_PATH = DATA_DIR / "web.sqlite3"
PART_LIMIT = 5 * 1024 * 1024


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    return re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE).strip("-")[:60] or "conversation"


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    source: str
    folder_name: str
    created_at: str
    updated_at: str
    status: str

    @property
    def folder(self) -> Path:
        return CONVERSATIONS_DIR / self.folder_name

    def json(self) -> dict:
        return asdict(self)


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    folder_name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'paused');
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_web_messages ON messages(conversation_id,id);
                UPDATE conversations SET status='paused';
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def row(value) -> Conversation | None:
        return Conversation(**dict(value)) if value else None

    def list(self) -> list[Conversation]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [self.row(row) for row in rows]

    def get(self, cid: str) -> Conversation | None:
        with self.connect() as db:
            return self.row(db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone())

    def create(self, title: str = "iPhone microphone") -> Conversation:
        stamp, cid = now(), uuid.uuid4().hex
        folder_name = f"{safe_name(title)}_{datetime.now():%Y-%m-%d_%H-%M-%S}_{cid[:8]}"
        folder = CONVERSATIONS_DIR / folder_name
        folder.mkdir(parents=True)
        with self.connect() as db:
            db.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?,?)",
                       (cid, title.strip() or "iPhone microphone", "Web microphone", folder_name, stamp, stamp, "paused"))
        return self.get(cid)

    def rename(self, cid: str, title: str) -> Conversation:
        item = self.get(cid)
        if not item or not title.strip():
            raise ValueError("Conversation or title is invalid")
        new_name = f"{safe_name(title)}_{item.folder_name.rsplit('_', 3)[-3]}_{item.folder_name.rsplit('_', 2)[-2]}_{cid[:8]}"
        if new_name != item.folder_name:
            item.folder.rename(CONVERSATIONS_DIR / new_name)
        with self.connect() as db:
            db.execute("UPDATE conversations SET title=?,folder_name=?,updated_at=? WHERE id=?",
                       (title.strip(), new_name, now(), cid))
        return self.get(cid)

    def status(self, cid: str, value: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE conversations SET status=?,updated_at=? WHERE id=?", (value, now(), cid))

    def add_message(self, cid: str, role: str, kind: str, content: str) -> None:
        stamp = now()
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,kind,content,created_at) VALUES(?,?,?,?,?)",
                       (cid, role, kind, content, stamp))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, cid))

    def messages(self, cid: str, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM messages WHERE conversation_id=?", [cid]
        if kind:
            sql, args = sql + " AND kind=?", [cid, kind]
        with self.connect() as db:
            rows = db.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(row) for row in rows]


class Transcript:
    def __init__(self, item: Conversation, store: Store):
        self.item, self.store, self.line_open = item, store, False

    def parts(self) -> list[Path]:
        first = self.item.folder / "transcript.txt"
        return ([first] if first.exists() else []) + sorted(self.item.folder.glob("transcript-[0-9][0-9][0-9][0-9].txt"))

    def path(self) -> Path:
        parts = self.parts()
        path = parts[-1] if parts else self.item.folder / "transcript.txt"
        if path.exists() and path.stat().st_size >= PART_LIMIT:
            path = self.item.folder / f"transcript-{len(parts)+1:04d}.txt"
        return path

    def begin(self) -> None:
        path = self.path()
        with path.open("a", encoding="utf-8") as f:
            if path.stat().st_size == 0:
                f.write("# Source: Web microphone\n")
            f.write(f"\n# Continued: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        self.line_open = False

    def write(self, text: str, end: bool) -> None:
        path = self.path()
        with path.open("a", encoding="utf-8") as f:
            if path.stat().st_size == 0:
                f.write("# Source: Web microphone\n\n")
            if not self.line_open:
                f.write(datetime.now().strftime("[%Y-%m-%d %H:%M:%S] "))
            f.write(text + ("\n" if end else " "))
        self.line_open = not end

    def read(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace").rstrip() for p in self.parts()).strip()

    def snapshot(self, last_lines: int = 0) -> Path:
        lines = self.read().splitlines()
        captions = [line for line in lines if line.startswith("[")]
        if last_lines and len(captions) > last_lines:
            path = self.item.folder / ".web-context.txt"
            path.write_text("\n".join(captions[-last_lines:]) + "\n", encoding="utf-8")
            return path
        parts = self.parts()
        if len(parts) == 1:
            return parts[0]
        path = self.item.folder / ".web-context.txt"
        path.write_text(self.read() + "\n", encoding="utf-8")
        return path
