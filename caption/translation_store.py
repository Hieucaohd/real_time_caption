"""Transcript-line index and append-only translation attempts in the app SQLite DB."""

from __future__ import annotations

import re
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from .shared_access import process_alive

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
                CREATE TABLE IF NOT EXISTS realtime_translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    passage_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    source_text TEXT NOT NULL, translated_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, passage_key)
                );
                CREATE INDEX IF NOT EXISTS realtime_reading ON realtime_translations(conversation_id,fingerprint,id);
            """)
            # Existing databases keep all their single-line translation history.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(line_translations)")}
            for name, definition in (("batch_source", "TEXT NOT NULL DEFAULT ''"),
                                     ("batch_size", "INTEGER NOT NULL DEFAULT 1"),
                                     ("batch_id", "TEXT NOT NULL DEFAULT ''"),
                                     ("owner_pid", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE line_translations ADD COLUMN {name} {definition}")

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

    def save_realtime(self, conversation_id, passage_key, config, source, text):
        with self.connect() as db:
            db.execute('''INSERT INTO realtime_translations
                (conversation_id,passage_key,fingerprint,source_text,translated_text,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id,passage_key) DO UPDATE SET
                source_text=excluded.source_text, translated_text=excluded.translated_text''',
                (conversation_id, passage_key, config.fingerprint, source, text, datetime.now().isoformat()))

    def google_source(self, conversation_id, config):
        with self.connect() as db:
            row = db.execute('SELECT source_text FROM realtime_translations WHERE conversation_id=? AND passage_key=?',
                (conversation_id, 'google-sync:' + config.fingerprint)).fetchone()
            return row['source_text'] if row else ''

    def reading(self, conversation_id, config):
        """Latest successful text in source order, without repeating batch answers."""
        if config.engine == 'google_web':
            lines = self.lines(conversation_id)
            with self.connect() as db:
                row = db.execute('SELECT * FROM realtime_translations WHERE conversation_id=? AND passage_key=?',
                    (conversation_id, 'google-sync:' + config.fingerprint)).fetchone()
            if row:
                # The result belongs to the entire paragraph, never to arbitrary
                # individual sentences. Only highlight fully synchronized lines.
                ids, prefix = [], ''
                for line in lines:
                    prefix += ('\n' if prefix else '') + line['source_text']
                    if not (row['source_text'] == prefix or row['source_text'].startswith(prefix + '\n')):
                        break
                    ids.append(line['id'])
                return {'segments': [{'line_id': 'google-sync', 'line_ids': ids, 'text': row['translated_text']}],
                        'realtime': [], 'translated_lines': len(ids), 'total_lines': len(lines),
                        'synced_source': row['source_text'], 'paragraph_sync': True}
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT t.* FROM line_translations t JOIN translation_lines l ON l.id=t.line_id "
                "WHERE l.conversation_id=? AND t.fingerprint=? ORDER BY t.id",
                (conversation_id, config.fingerprint))]
            sources = {row['id']: row['source_text'] for row in db.execute(
                'SELECT id, source_text FROM translation_lines WHERE conversation_id=? ORDER BY id', (conversation_id,))}
            realtime = [dict(row) for row in db.execute(
                'SELECT id, source_text, translated_text AS text FROM realtime_translations '
                'WHERE conversation_id=? AND fingerprint=? ORDER BY id', (conversation_id, config.fingerprint))]
        latest, legacy_counts, group_members = {}, {}, {}
        for row in rows:
            group = row['batch_id'] or str(row['id'])
            if not row['batch_id'] and row['batch_size'] > 1:
                # Old versions stored one attempt per member, with identical
                # batch metadata. Count complete groups so repeated utterances
                # with the same text and timestamp remain separate passages.
                signature = (row['created_at'], row['batch_source'], row['batch_size'])
                count = legacy_counts.get(signature, 0)
                legacy_counts[signature] = count + 1
                group = ('legacy', signature, count // row['batch_size'])
            if row['status'] == 'done' and row['source_text'] == sources.get(row['line_id']):
                group_members.setdefault(group, []).append(row['line_id'])
                latest[row['line_id']] = (group, row['translated_text'])
        segments, seen = [], set()
        for line_id in sources:
            if line_id not in latest:
                continue
            group, text = latest[line_id]
            if group not in seen:
                seen.add(group)
                segments.append({'line_id': line_id, 'line_ids': group_members[group], 'text': text})
        return {'segments': segments, 'realtime': realtime, 'translated_lines': len(latest), 'total_lines': len(sources)}

    def missing(self, conversation_id, config):
        if config.engine == 'google_web':
            lines = self.lines(conversation_id)
            return [] if '\n'.join(line['source_text'] for line in lines) == self.google_source(conversation_id, config) else lines
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
        return self.begin_batch([line], config)[0]

    def begin_batch(self, lines, config):
        # Each source line links to the shared result and its full source context.
        # Never claim that an arbitrary paragraph is a per-sentence alignment.
        source = "\n".join(line["source_text"] for line in lines) if len(lines) > 1 else ""
        attempts = []
        batch_id = uuid.uuid4().hex
        with self.connect() as db:
            for line in lines:
                cursor = db.execute("INSERT INTO line_translations(line_id,engine,model,source_lang,target_lang,fingerprint,source_text,status,created_at,batch_source,batch_size) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    (line["id"], config.engine, config.model, config.source, config.target,
                                     config.fingerprint, line["source_text"], "queued", datetime.now().isoformat(timespec="seconds"), source, len(lines)))
                attempts.append(cursor.lastrowid)
                db.execute('UPDATE line_translations SET owner_pid=?,batch_id=? WHERE id=?', (os.getpid(), batch_id, cursor.lastrowid))
        return attempts

    def finish(self, attempt, status, text="", error=""):
        with self.connect() as db:
            db.execute("UPDATE line_translations SET status=?,translated_text=?,error=? WHERE id=?", (status, text, error, attempt))

    def recover(self):
        with self.connect() as db:
            owners = db.execute("SELECT DISTINCT owner_pid FROM line_translations WHERE status IN ('queued','working')").fetchall()
            for owner in owners:
                if not process_alive(owner['owner_pid']):
                    db.execute("UPDATE line_translations SET status='cancelled',error='App closed before completion; retry manually.' WHERE status IN ('queued','working') AND owner_pid=?", (owner['owner_pid'],))
