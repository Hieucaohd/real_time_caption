"""Persistent question/answer trees in the native conversation database."""
from __future__ import annotations

import json
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from .shared_access import process_alive, BusyError

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
                CREATE TABLE IF NOT EXISTS research_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    config TEXT NOT NULL
                );
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(research_nodes)')}
            for name, definition in (('hidden', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('response_tab_title', "TEXT NOT NULL DEFAULT ''"),
                                     ('response_conversation_url', "TEXT NOT NULL DEFAULT ''"),
                                     ('response_turn_id', "TEXT NOT NULL DEFAULT ''"),
                                     ('response_message_id', "TEXT NOT NULL DEFAULT ''"),
                                     ('response_position', 'INTEGER CHECK(response_position >= 1)'),
                                     ('reading_mark', 'TEXT'),
                                     ('excerpt_mark', 'TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE research_nodes ADD COLUMN {name} {definition}')
            owners = db.execute("SELECT DISTINCT owner_pid FROM research_nodes WHERE status='working'").fetchall()
            for owner in owners:
                if not process_alive(owner['owner_pid']):
                    db.execute("UPDATE research_nodes SET status='interrupted', detail=? WHERE status='working' AND owner_pid=?",
                               ('Server stopped before completion. Retry manually to avoid sending twice.', owner['owner_pid']))
        self.archive_legacy_attachments()

    def defaults(self):
        with self.conversations._connect() as db:
            row = db.execute('SELECT config FROM research_preferences WHERE id=1').fetchone()
        return json.loads(row['config']) if row else {}

    def save_defaults(self, config):
        with self.conversations._connect() as db:
            db.execute('''INSERT INTO research_preferences(id, config) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET config=excluded.config''',
                (json.dumps(config, ensure_ascii=False),))
        return config

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
        result['hidden'] = bool(result['hidden'])
        result['reading_mark'] = json.loads(result['reading_mark']) if result['reading_mark'] else None
        result['excerpt_mark'] = json.loads(result['excerpt_mark']) if result['excerpt_mark'] else None
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

    def branch_marks(self, cid, nid):
        """Read-only links to direct children; deleting/renaming children stays in sync."""
        with self.conversations._connect() as db:
            rows = db.execute('''SELECT id, title, hidden, excerpt, excerpt_mark FROM research_nodes
                WHERE conversation_id=? AND parent_id=? AND excerpt != '' ORDER BY created_at, rowid''', (cid, nid))
            return [dict(id=row['id'], title=row['title'], hidden=bool(row['hidden']), excerpt=row['excerpt'],
                         mark=json.loads(row['excerpt_mark']) if row['excerpt_mark'] else None) for row in rows]

    def link_child(self, cid, nid, child_id, excerpt, mark):
        """Move a direct child's source link without changing its frozen request."""
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = self.row(db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?',
                                        (nid, cid)).fetchone())
            child = self.row(db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?',
                                       (child_id, cid)).fetchone())
            if parent['status'] != 'done':
                raise ValueError('Chờ câu trả lời của node cha hoàn tất trước khi liên kết.')
            if child['parent_id'] != nid:
                raise ValueError('Chỉ có thể liên kết tới node con trực tiếp của node đang đọc.')
            db.execute('UPDATE research_nodes SET excerpt=?, excerpt_mark=?, updated_at=? WHERE id=? AND conversation_id=?',
                       (excerpt, json.dumps(mark, ensure_ascii=False), now(), child_id, cid))
            return self.row(db.execute('SELECT * FROM research_nodes WHERE id=?', (child_id,)).fetchone())

    def import_answer(self, cid, mid):
        messages = self.conversations.messages(cid)
        source = next((m for m in messages if m.id == mid and m.role == 'bot' and m.kind == 'chat'), None)
        if not source or not source.content.strip() or source.header.startswith('Error'):
            raise ValueError('Choose a completed Chat answer to start a research tree.')
        question = next((m.content for m in reversed(messages) if m.id < mid and m.role == 'user' and m.kind == 'chat'), 'Explore this answer')
        nid, stamp = uuid.uuid4().hex, now()
        with self.conversations._connect() as db:
            db.execute('''INSERT OR IGNORE INTO research_nodes
                (id, conversation_id, root_id, source_message_id, title, question, answer, status, created_at, updated_at, response_tab_title,
                 response_conversation_url, response_turn_id, response_message_id, response_position)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'done', ?, ?, ?, ?, ?, ?, ?)''',
                (nid, cid, nid, mid, question.strip().splitlines()[0][:100], question, source.content, stamp, stamp, source.response_tab_title,
                 source.response_conversation_url, source.response_turn_id, source.response_message_id, source.response_position))
            return self.row(db.execute('SELECT * FROM research_nodes WHERE source_message_id=?', (mid,)).fetchone())

    def ancestry(self, cid, nid):
        chain, seen = [], set()
        with self.conversations._connect() as db:
            db.execute('BEGIN')
            while nid:
                if nid in seen:
                    raise ValueError('Invalid research tree')
                seen.add(nid)
                node = self.row(db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?', (nid, cid)).fetchone())
                chain.append(node)
                nid = node['parent_id']
        return list(reversed(chain))

    def update_response_source(self, cid, source, *, mid=None, nid=None):
        fields = {'response_conversation_url', 'response_position'}
        if set(source) != fields or (mid is None) == (nid is None):
            raise ValueError('Invalid response source update')
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if nid is not None:
                node = db.execute('SELECT status, source_message_id FROM research_nodes WHERE id=? AND conversation_id=?', (nid, cid)).fetchone()
                if node is None:
                    raise KeyError('Research node not found')
                if node['status'] in ('queued', 'working'):
                    raise ValueError('Chờ câu trả lời hoàn tất trước khi cập nhật nguồn.')
                mid = node['source_message_id']
            assignments = ', '.join(f'{key}=?' for key in source)
            values = tuple(source.values())
            if mid is not None:
                message = db.execute('SELECT role FROM chat_messages WHERE id=? AND conversation_id=?', (mid, cid)).fetchone()
                if message is None:
                    raise KeyError('Chat answer not found')
                if message['role'] != 'bot':
                    raise ValueError('Chỉ cập nhật nguồn cho câu trả lời ChatGPT.')
                db.execute('UPDATE chat_messages SET ' + assignments + ' WHERE id=? AND conversation_id=?', (*values, mid, cid))
                # An imported root and its source Chat message refer to the same response.
                db.execute('UPDATE research_nodes SET ' + assignments + ', updated_at=? WHERE source_message_id=? AND conversation_id=?',
                           (*values, now(), mid, cid))
            else:
                db.execute('UPDATE research_nodes SET ' + assignments + ', updated_at=? WHERE id=? AND conversation_id=?',
                           (*values, now(), nid, cid))
        return source

    @staticmethod
    def _subtree(db, cid, nid):
        rows = db.execute('''WITH RECURSIVE branch(id) AS (
            SELECT id FROM research_nodes WHERE id=? AND conversation_id=?
            UNION SELECT n.id FROM research_nodes n JOIN branch b ON n.parent_id=b.id
                WHERE n.conversation_id=?
            ) SELECT n.* FROM research_nodes n JOIN branch b ON n.id=b.id''', (nid, cid, cid)).fetchall()
        if not rows:
            raise KeyError('Research node not found')
        return rows

    def set_hidden(self, cid, nid, hidden):
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._subtree(db, cid, nid)
            ids = {row['id'] for row in rows}
            # Restoring a nested node also reveals its path, so it cannot be orphaned.
            if not hidden:
                parent = next(row['parent_id'] for row in rows if row['id'] == nid)
                while parent and parent not in ids:
                    row = db.execute('SELECT parent_id FROM research_nodes WHERE id=? AND conversation_id=?', (parent, cid)).fetchone()
                    if row is None:
                        break
                    ids.add(parent)
                    parent = row['parent_id']
            db.executemany('UPDATE research_nodes SET hidden=?, updated_at=? WHERE id=? AND conversation_id=?',
                           [(int(hidden), now(), node_id, cid) for node_id in ids])
        return dict(ids=sorted(ids), hidden=hidden)

    def delete_subtree(self, cid, nid):
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._subtree(db, cid, nid)
            if any(row['status'] == 'working' and row['owner_pid'] != os.getpid()
                   and process_alive(row['owner_pid']) for row in rows):
                raise BusyError('Nhánh đang chạy ở tiến trình khác. Hủy yêu cầu ở đó trước khi xóa.')
            ids = {row['id'] for row in rows}
            # One statement removes all descendants before FK checks; attachments/layouts cascade.
            db.execute('DELETE FROM research_nodes WHERE id IN (' + ','.join('?' for _ in ids) + ')', tuple(ids))
            for root_id in {row['root_id'] for row in rows} - ids:
                layout = db.execute('SELECT state FROM research_graph_layouts WHERE root_id=?', (root_id,)).fetchone()
                if layout:
                    state = json.loads(layout['state'])
                    state['positions'] = {key: value for key, value in state.get('positions', {}).items() if key not in ids}
                    state['collapsed'] = [key for key in state.get('collapsed', []) if key not in ids]
                    db.execute('UPDATE research_graph_layouts SET state=?, revision=revision+1, updated_at=? WHERE root_id=?',
                               (json.dumps(state), now(), root_id))
            db.execute('DELETE FROM research_assets WHERE NOT EXISTS (SELECT 1 FROM research_node_assets n WHERE n.asset_id=research_assets.id)')
        return dict(ids=sorted(ids))

    def add_children(self, cid, parent, children, parent_reading_mark=None):
        with self.conversations._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            parent = self.row(db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?', (parent['id'], cid)).fetchone())
            known_assets = {}
            for child in children:
                stamp = now()
                db.execute('''INSERT INTO research_nodes
                    (id, conversation_id, parent_id, root_id, title, question, excerpt, config, request, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (child['id'], cid, parent['id'], parent['root_id'], child['title'], child['question'], child['excerpt'],
                     json.dumps(child['config'], ensure_ascii=False), json.dumps(child['request'], ensure_ascii=False), stamp, stamp))
                self._archive_assets(db, child['id'], child['request'], known_assets)
                if parent_reading_mark is not None:
                    db.execute('UPDATE research_nodes SET excerpt_mark=? WHERE id=?',
                               (json.dumps(parent_reading_mark, ensure_ascii=False), child['id']))
                if parent['hidden']:
                    db.execute('UPDATE research_nodes SET hidden=1 WHERE id=?', (child['id'],))
            if parent_reading_mark is not None:
                db.execute('UPDATE research_nodes SET reading_mark=?, updated_at=? WHERE id=? AND conversation_id=?',
                           (json.dumps(parent_reading_mark, ensure_ascii=False), now(), parent['id'], cid))
        return [self.get(cid, child['id']) for child in children]

    def progress(self, cid, nid, **values):
        if not values or not values.keys() <= {'partial', 'detail'}:
            raise ValueError('Invalid progress update')
        values['updated_at'] = now()
        with self.conversations._connect() as db:
            db.execute('UPDATE research_nodes SET ' + ', '.join(f'{key}=?' for key in values)
                       + " WHERE id=? AND conversation_id=? AND status='working'",
                       (*values.values(), nid, cid))

    def next_queued(self, excluded_tabs=()):
        with self.conversations._connect() as db:
            for row in db.execute("SELECT * FROM research_nodes WHERE status='queued' ORDER BY created_at, rowid"):
                node = self.row(row, private=True)
                if node['config'].get('selected_tab') not in excluded_tabs:
                    return node
        return None

    def claim(self, cid, nid):
        with self.conversations._connect() as db:
            return db.execute("UPDATE research_nodes SET status='working', owner_pid=?, detail='Preparing…', partial='' WHERE id=? AND conversation_id=? AND status='queued'",
                              (os.getpid(), nid, cid)).rowcount == 1

    def update(self, cid, nid, **values):
        allowed = {'status', 'detail', 'answer', 'partial', 'understood', 'title', 'config', 'request', 'response_tab_title', 'reading_mark',
                   'response_conversation_url', 'response_turn_id', 'response_message_id', 'response_position'}
        if not values or not values.keys() <= allowed:
            raise ValueError('Invalid node update')
        for key in ('config', 'request', 'reading_mark'):
            if key in values and values[key] is not None:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values['updated_at'] = now()
        with self.conversations._connect() as db:
            db.execute('UPDATE research_nodes SET ' + ', '.join(f'{key}=?' for key in values) + ' WHERE id=? AND conversation_id=?',
                       (*values.values(), nid, cid))
        return self.get(cid, nid)
