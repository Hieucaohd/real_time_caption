"""Conversation-scoped realtime chat state and history in the shared SQLite DB."""
import json
from datetime import datetime


class RealtimeChatStore:
    def __init__(self, conversations):
        self.conversations = conversations
        with conversations._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS realtime_chat_settings (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
                    config TEXT NOT NULL,
                    actions_cursor TEXT NOT NULL DEFAULT '{}',
                    summary_cursor TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS realtime_chat_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, question TEXT NOT NULL DEFAULT '',
                    answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', files TEXT NOT NULL DEFAULT '[]',
                    response_source TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS realtime_chat_history ON realtime_chat_events(conversation_id,id);
            ''')

    def recover(self):
        with self.conversations._connect() as db:
            db.execute("UPDATE realtime_chat_events SET status='interrupted',detail='Ứng dụng đã đóng trước khi nhận xong câu trả lời.' WHERE status IN ('queued','working','received')")

    def settings(self, cid, defaults):
        with self.conversations._connect() as db:
            row = db.execute('SELECT * FROM realtime_chat_settings WHERE conversation_id=?', (cid,)).fetchone()
        return ({**defaults, **json.loads(row['config'])},
                {lane: json.loads(row[lane+'_cursor']) for lane in ('actions', 'summary')}) if row else (defaults, {'actions': {}, 'summary': {}})

    def save(self, cid, config, reset=()):
        with self.conversations._connect() as db:
            db.execute('INSERT INTO realtime_chat_settings(conversation_id,config) VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET config=excluded.config',
                (cid, json.dumps(config, ensure_ascii=False)))
            for lane in reset:
                assert lane in ('actions', 'summary')
                db.execute(f"UPDATE realtime_chat_settings SET {lane}_cursor='{{}}' WHERE conversation_id=?", (cid,))

    def create(self, cid, kind, question='', files=()):
        with self.conversations._connect() as db:
            return db.execute('INSERT INTO realtime_chat_events(conversation_id,kind,question,status,files,created_at) VALUES(?,?,?,?,?,?)',
                (cid, kind, question, 'queued', json.dumps(list(files)), datetime.now().isoformat(timespec='seconds'))).lastrowid

    def update(self, eid, status, answer='', detail='', source=None, files=None, cursor=None, lane=None):
        with self.conversations._connect() as db:
            db.execute('UPDATE realtime_chat_events SET status=?,answer=?,detail=?,response_source=? WHERE id=?',
                (status, answer, detail, json.dumps(source or {}), eid))
            if files is not None:
                db.execute('UPDATE realtime_chat_events SET files=? WHERE id=?', (json.dumps(files), eid))
            if cursor is not None:
                assert lane in ('actions', 'summary')
                db.execute(f'UPDATE realtime_chat_settings SET {lane}_cursor=? WHERE conversation_id=(SELECT conversation_id FROM realtime_chat_events WHERE id=?)',
                    (json.dumps(cursor), eid))

    def latest_actions(self, cid):
        with self.conversations._connect() as db:
            row = db.execute("SELECT answer FROM realtime_chat_events WHERE conversation_id=? AND kind='actions' AND status='done' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        return json.loads(row['answer']) if row else []

    def history(self, cid, before=None):
        with self.conversations._connect() as db:
            rows = db.execute('SELECT * FROM realtime_chat_events WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 200',
                              (cid, before if before is not None else 2**63-1)).fetchall()
        result = []
        for row in reversed(rows):
            value = dict(row)
            value['files'] = json.loads(value['files'])
            value['response_source'] = json.loads(value['response_source'])
            result.append(value)
        return result


def transcript_delta(lines, cursor, initial_limit=0, max_chars=12000):
    """A cursor belongs to one ChatGPT tab; cap each batch without dropping backlog."""
    start, offset = 0, 0
    if cursor:
        match = next((i for i, row in enumerate(lines) if row['origin'] == cursor.get('origin')), None)
        if match is None:
            raise ValueError('Không tìm thấy mốc transcript đã gửi. Hãy chọn tab ChatGPT mới để bắt đầu lại.')
        start, offset = match, int(cursor.get('offset', 0))
    elif initial_limit:
        start = max(0, len(lines) - initial_limit)
    parts, size, end = [], 0, dict(cursor)
    for row in lines[start:]:
        text = row['source_text']
        tail = text[offset:]
        if tail:
            room = max_chars - size - (1 if parts else 0)
            if room <= 0:
                break
            piece = tail[:room]
            parts.append(piece)
            size += len(piece) + (1 if len(parts) > 1 else 0)
            end = {'origin': row['origin'], 'offset': offset + len(piece)}
            if len(piece) < len(tail):
                break
        offset = 0
    return '\n'.join(parts), end
