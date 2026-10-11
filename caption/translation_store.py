"""Transcript-line index and append-only translation attempts in the app SQLite DB."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

_CAPTION = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


class TranslationStore:
    UPSERT_LINE = (
        "INSERT INTO translation_lines(conversation_id,origin,stamp,source_text) VALUES(?,?,?,?) "
        "ON CONFLICT(conversation_id,origin) DO UPDATE SET source_text=excluded.source_text "
        "WHERE translation_lines.source_text<>excluded.source_text"
    )
    def __init__(self, db_path):
        self.db_path = db_path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS translation_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    origin TEXT NOT NULL, stamp TEXT NOT NULL, source_text TEXT NOT NULL,
                    UNIQUE(conversation_id, origin)
                );
                CREATE TABLE IF NOT EXISTS line_translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    line_id INTEGER NOT NULL REFERENCES translation_lines(id) ON DELETE CASCADE,
                    engine TEXT NOT NULL, model TEXT NOT NULL, source_lang TEXT NOT NULL,
                    target_lang TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    source_text TEXT NOT NULL, translated_text TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS translation_history ON line_translations(line_id,id);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def line(self, conversation_id, origin, stamp, text):
        with self.connect() as db:
            db.execute(self.UPSERT_LINE, (conversation_id, origin, stamp, text.strip()))
            return dict(db.execute("SELECT * FROM translation_lines WHERE conversation_id=? AND origin=?", (conversation_id, origin)).fetchone())

    def sync_transcript(self, transcript):
        # Stable byte offsets work across resume, rename and transcript splitting.
        # No file is changed and translations of identical repeated lines remain distinct.
        with self.connect() as db:
            for path in transcript.parts():
                offset = 0
                with path.open("rb") as stream:
                    for raw in stream:
                        match = _CAPTION.match(raw.decode("utf-8", errors="replace").rstrip())
                        if match and match[2].strip():
                            db.execute(self.UPSERT_LINE, (transcript.conversation_id, f"{path.name}:{offset}", match[1], match[2].strip()))
                        offset += len(raw)

    def lines(self, conversation_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT l.*, (SELECT engine||': '||status FROM line_translations t WHERE t.line_id=l.id ORDER BY t.id DESC LIMIT 1) AS last_state "
                "FROM translation_lines l WHERE conversation_id=? ORDER BY l.id", (conversation_id,))]

    def get_line(self, line_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM translation_lines WHERE id=?", (line_id,)).fetchone()
            return dict(row) if row else None

    def missing(self, conversation_id, config):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT l.* FROM translation_lines l WHERE conversation_id=? AND NOT EXISTS ("
                "SELECT 1 FROM line_translations t WHERE t.line_id=l.id AND t.fingerprint=? "
                "AND t.source_text=l.source_text AND t.status='done') ORDER BY l.id",
                (conversation_id, config.fingerprint))]

    def history(self, line_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM line_translations WHERE line_id=? ORDER BY id", (line_id,))]

    def has_success(self, line, config):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM line_translations WHERE line_id=? AND fingerprint=? AND source_text=? AND status='done' LIMIT 1",
                              (line["id"], config.fingerprint, line["source_text"])).fetchone() is not None

    def begin(self, line, config):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO line_translations(line_id,engine,model,source_lang,target_lang,fingerprint,source_text,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                (line["id"], config.engine, config.model, config.source, config.target,
                                 config.fingerprint, line["source_text"], "queued", datetime.now().isoformat(timespec="seconds")))
            return cursor.lastrowid

    def finish(self, attempt, status, text="", error=""):
        with self.connect() as db:
            db.execute("UPDATE line_translations SET status=?,translated_text=?,error=? WHERE id=?", (status, text, error, attempt))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE line_translations SET status='cancelled',error='App closed before completion; retry manually.' WHERE status IN ('queued','working')")
