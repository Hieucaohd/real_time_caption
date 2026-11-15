"""Persistent question/answer trees in the native conversation database."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from .shared_access import process_alive

log = logging.getLogger(__name__)


def now():
    return datetime.now().isoformat(timespec='milliseconds')


class ResearchStore:
    def __init__(self, conversations):
        self.conversations = conversations
        with conversations._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS research_nodes (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id),
                    parent_id TEXT REFERENCES research_nodes(id),
                    root_id TEXT NOT NULL,
                    source_message_id INTEGER UNIQUE REFERENCES chat_messages(id),
                    title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '',
                    excerpt TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    detail TEXT NOT NULL DEFAULT '',
                    partial TEXT NOT NULL DEFAULT '',
                    understood INTEGER NOT NULL DEFAULT 0,
                    config TEXT NOT NULL DEFAULT '{}',
                    request TEXT NOT NULL DEFAULT '{}',
                    owner_pid INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_conversation ON research_nodes(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS research_queue ON research_nodes(status, created_at);
                CREATE TABLE IF NOT EXISTS research_assets (
                    id TEXT PRIMARY KEY,
                    content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_node_assets (
                    node_id TEXT NOT NULL REFERENCES research_nodes(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    asset_id TEXT NOT NULL REFERENCES research_assets(id),
                    filename TEXT NOT NULL,
                    PRIMARY KEY(node_id, position)
                );
                CREATE TABLE IF NOT EXISTS research_graph_layouts (
                    root_id TEXT PRIMARY KEY REFERENCES research_nodes(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
            ''')
            owners = db.execute("SELECT DISTINCT owner_pid FROM research_nodes WHERE status='working'").fetchall()
            for owner in owners:
                if not process_alive(owner['owner_pid']):
                    db.execute("UPDATE research_nodes SET status='interrupted', detail=? WHERE status='working' AND owner_pid=?",
                               ('Server stopped before completion. Retry manually to avoid sending twice.', owner['owner_pid']))
        self.archive_legacy_attachments()

    def graph_layout(self, cid, rid):
        root = self.get(cid, rid)
        if root['parent_id'] is not None:
            raise ValueError('Layout belongs to the root of a research tree.')
        with self.conversations._connect() as db:
            row = db.execute('SELECT * FROM research_graph_layouts WHERE root_id=?', (rid,)).fetchone()
        defaults = dict(positions={}, camera=None, collapsed=[], snap=True, wheel_zoom=True, mode='select', move_subtree=False)
        return {**defaults, **(json.loads(row['state']) if row else {}), 'revision': row['revision'] if row else 0}

    def save_graph_layout(self, cid, rid, changes):
        # Merge only the moved nodes, so another tab's unrelated positions survive.
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            root = db.execute('SELECT parent_id FROM research_nodes WHERE id=? AND conversation_id=?', (rid, cid)).fetchone()
            if root is None:
                raise KeyError('Research tree not found')
            if root['parent_id'] is not None:
                raise ValueError('Layout belongs to the root of a research tree.')
            ids = {row['id'] for row in db.execute('SELECT id FROM research_nodes WHERE conversation_id=? AND root_id=?', (cid, rid))}
            if not set(changes.get('positions', {})) <= ids or not set(changes.get('collapsed', [])) <= ids:
                raise ValueError('Layout contains nodes outside this research tree.')
            row = db.execute('SELECT state FROM research_graph_layouts WHERE root_id=?', (rid,)).fetchone()
            state = json.loads(row['state']) if row else {}
            if 'positions' in changes:
                state['positions'] = {**state.get('positions', {}), **changes['positions']}
            state.update({key: value for key, value in changes.items() if key != 'positions'})
            db.execute('''INSERT INTO research_graph_layouts(root_id, state, revision, updated_at) VALUES (?, ?, 1, ?)
                ON CONFLICT(root_id) DO UPDATE SET state=excluded.state, revision=revision+1, updated_at=excluded.updated_at''',
                (rid, json.dumps(state, ensure_ascii=False), now()))
        return self.graph_layout(cid, rid)

    @staticmethod
    def _archive_assets(db, nid, request, known):
        for position, value in enumerate(request.get('attachments', [])):
            path = Path(value)
            if value not in known:
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                db.execute('INSERT OR IGNORE INTO research_assets(id, content) VALUES (?, ?)', (digest, content))
                known[value] = digest
            db.execute('INSERT OR REPLACE INTO research_node_assets(node_id, position, asset_id, filename) VALUES (?, ?, ?, ?)',
                       (nid, position, known[value], path.name))
        request = {**request, 'assets_in_database': True}
        db.execute('UPDATE research_nodes SET request=? WHERE id=?', (json.dumps(request, ensure_ascii=False), nid))

    def archive_legacy_attachments(self):
        """Backfill existing snapshots without changing their frozen prompts or answers."""
        with self.conversations._connect() as db:
            rows = db.execute('SELECT id, request FROM research_nodes WHERE parent_id IS NOT NULL').fetchall()
            for row in rows:
                request = json.loads(row['request'])
                if request.get('assets_in_database'):
                    continue
                db.execute('SAVEPOINT research_asset_migration')
                try:
                    self._archive_assets(db, row['id'], request, {})
                except OSError:
                    db.execute('ROLLBACK TO research_asset_migration')
                    log.warning('Cannot archive missing/unreadable legacy attachment for research node %s', row['id'])
                finally:
                    db.execute('RELEASE research_asset_migration')

    def restore_attachments(self, cid, nid, folder):
        """SQLite is authoritative; rebuild disposable upload files before each attempt."""
        node = self.get(cid, nid, private=True)
        if not node['request'].get('assets_in_database'):
            with self.conversations._connect() as db:
                self._archive_assets(db, nid, node['request'], {})
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        result = []
        with self.conversations._connect() as db:
            rows = db.execute('''SELECT n.filename, a.content FROM research_node_assets n
                JOIN research_assets a ON a.id=n.asset_id WHERE n.node_id=? ORDER BY n.position''', (nid,))
            for row in rows:
                target = folder / Path(row['filename']).name
                if target in result:
                    target = folder / f'{len(result)}_{target.name}'
                target.write_bytes(row['content'])
                result.append(target)
        if len(result) != len(node['request'].get('attachments', [])):
            raise ValueError('Stored research attachments are incomplete. Nothing was sent.')
        return result

    @staticmethod
    def row(value, private=False):
        if value is None:
            raise KeyError('Research node not found')
        result = dict(value)
        result['config'] = json.loads(result['config'])
        result['understood'] = bool(result['understood'])
        if private:
            result['request'] = json.loads(result['request'])
        else:
            result.pop('request')
        return result

    def get(self, cid, nid, private=False):
        with self.conversations._connect() as db:
            return self.row(db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?', (nid, cid)).fetchone(), private)

    def list(self, cid):
        with self.conversations._connect() as db:
            return [self.row(row) for row in db.execute('SELECT * FROM research_nodes WHERE conversation_id=? ORDER BY created_at, rowid', (cid,))]

    def import_answer(self, cid, mid):
        messages = self.conversations.messages(cid)
        source = next((m for m in messages if m.id == mid and m.role == 'bot' and m.kind == 'chat'), None)
        if not source or not source.content.strip() or source.header.startswith('Error'):
            raise ValueError('Choose a completed Chat answer to start a research tree.')
        question = next((m.content for m in reversed(messages) if m.id < mid and m.role == 'user' and m.kind == 'chat'), 'Explore this answer')
        nid, stamp = uuid.uuid4().hex, now()
        with self.conversations._connect() as db:
            db.execute('''INSERT OR IGNORE INTO research_nodes
                (id, conversation_id, root_id, source_message_id, title, question, answer, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'done', ?, ?)''',
                (nid, cid, nid, mid, question.strip().splitlines()[0][:100], question, source.content, stamp, stamp))
            return self.row(db.execute('SELECT * FROM research_nodes WHERE source_message_id=?', (mid,)).fetchone())

    def ancestry(self, cid, nid):
        chain, seen = [], set()
        while nid:
            if nid in seen:
                raise ValueError('Invalid research tree')
            seen.add(nid)
            node = self.get(cid, nid)
            chain.append(node)
            nid = node['parent_id']
        return list(reversed(chain))

    def add_children(self, cid, parent, children):
        with self.conversations._connect() as db:
            known_assets = {}
            for child in children:
                stamp = now()
                db.execute('''INSERT INTO research_nodes
                    (id, conversation_id, parent_id, root_id, title, question, excerpt, config, request, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (child['id'], cid, parent['id'], parent['root_id'], child['title'], child['question'], child['excerpt'],
                     json.dumps(child['config'], ensure_ascii=False), json.dumps(child['request'], ensure_ascii=False), stamp, stamp))
                self._archive_assets(db, child['id'], child['request'], known_assets)
        return [self.get(cid, child['id']) for child in children]

    def progress(self, cid, nid, **values):
        if not values or not values.keys() <= {'partial', 'detail'}:
            raise ValueError('Invalid progress update')
        values['updated_at'] = now()
        with self.conversations._connect() as db:
            db.execute('UPDATE research_nodes SET ' + ', '.join(f'{key}=?' for key in values)
                       + " WHERE id=? AND conversation_id=? AND status='working'",
                       (*values.values(), nid, cid))

    def next_queued(self):
        with self.conversations._connect() as db:
            row = db.execute("SELECT * FROM research_nodes WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            return self.row(row, private=True) if row else None

    def claim(self, cid, nid):
        with self.conversations._connect() as db:
            return db.execute("UPDATE research_nodes SET status='working', owner_pid=?, detail='Preparing…', partial='' WHERE id=? AND conversation_id=? AND status='queued'",
                              (os.getpid(), nid, cid)).rowcount == 1

    def update(self, cid, nid, **values):
        allowed = {'status', 'detail', 'answer', 'partial', 'understood', 'title', 'config', 'request'}
        if not values or not values.keys() <= allowed:
            raise ValueError('Invalid node update')
        for key in ('config', 'request'):
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values['updated_at'] = now()
        with self.conversations._connect() as db:
            db.execute('UPDATE research_nodes SET ' + ', '.join(f'{key}=?' for key in values) + ' WHERE id=? AND conversation_id=?',
                       (*values.values(), nid, cid))
        return self.get(cid, nid)
