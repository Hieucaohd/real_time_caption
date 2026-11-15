"""Persistent question/answer trees in the native conversation database."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from .shared_access import process_alive


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
            ''')
            owners = db.execute("SELECT DISTINCT owner_pid FROM research_nodes WHERE status='working'").fetchall()
            for owner in owners:
                if not process_alive(owner['owner_pid']):
                    db.execute("UPDATE research_nodes SET status='interrupted', detail=? WHERE status='working' AND owner_pid=?",
                               ('Server stopped before completion. Retry manually to avoid sending twice.', owner['owner_pid']))

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
            for child in children:
                stamp = now()
                db.execute('''INSERT INTO research_nodes
                    (id, conversation_id, parent_id, root_id, title, question, excerpt, config, request, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (child['id'], cid, parent['id'], parent['root_id'], child['title'], child['question'], child['excerpt'],
                     json.dumps(child['config'], ensure_ascii=False), json.dumps(child['request'], ensure_ascii=False), stamp, stamp))
        return [self.get(cid, child['id']) for child in children]

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
