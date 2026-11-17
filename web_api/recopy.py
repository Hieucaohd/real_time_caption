"""Refresh saved answer content without re-running its question."""
import hashlib
import logging

from caption import paths
from caption.chatgpt_recopy import recopy_response
from caption.research_store import now
from caption.shared_access import ProcessLock

FIELDS = ('response_tab_title', 'response_conversation_url', 'response_turn_id', 'response_message_id', 'response_position')


def _target(db, cid, mid, nid):
    if nid is not None:
        row = db.execute('SELECT * FROM research_nodes WHERE id=? AND conversation_id=?', (nid, cid)).fetchone()
        if row is None:
            raise KeyError('Research node not found')
        if row['status'] in ('queued', 'working'):
            raise ValueError('Hãy chờ hoặc hủy tác vụ đang chạy trước khi lấy lại câu trả lời.')
    else:
        row = db.execute("SELECT * FROM chat_messages WHERE id=? AND conversation_id=? AND role='bot' AND kind='chat'", (mid, cid)).fetchone()
        if row is None:
            raise KeyError('Chat answer not found')
    return dict(row)


def refresh_answer(service, cid, *, mid=None, nid=None, return_tab=''):
    service.conversation(cid)
    with service.store._connect() as db:
        before = _target(db, cid, mid, nid)
    linked_mid = mid if mid is not None else before['source_message_id']
    identity = f'{cid}:message:{linked_mid}' if linked_mid is not None else f'{cid}:node:{nid}'
    lock = paths.CACHE_DIR / ('recopy-' + hashlib.sha256(identity.encode()).hexdigest() + '.lock')
    with ProcessLock(lock):
        result = recopy_response(before['response_conversation_url'], before['response_position'], return_tab)
        if not result['content'].strip():
            raise ValueError('Câu trả lời vẫn rỗng. Nội dung đã lưu chưa thay đổi; hãy thử lại sau.')
        with service.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = _target(db, cid, mid, nid)
            check = (*FIELDS, 'answer', 'status', 'source_message_id') if nid is not None else (*FIELDS, 'content')
            if any(before[key] != current[key] for key in check):
                raise ValueError('Câu trả lời hoặc nguồn đã thay đổi trong lúc lấy lại. Hãy thử lại.')
            values = tuple(result[key] for key in FIELDS)
            assignments = ', '.join(f'{key}=?' for key in FIELDS)
            if linked_mid is not None:
                db.execute('UPDATE chat_messages SET content=?, ' + assignments + ' WHERE conversation_id=? AND id=?',
                           (result['content'], *values, cid, linked_mid))
                predicate, identifier = 'source_message_id', linked_mid
            else:
                predicate, identifier = 'id', nid
            db.execute('UPDATE research_nodes SET answer=?, ' + assignments +
                       ", status='done', partial='', detail='Đã lấy lại câu trả lời từ ChatGPT', updated_at=? "
                       f'WHERE conversation_id=? AND {predicate}=?', (result['content'], *values, now(), cid, identifier))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), cid))
        if linked_mid is not None:
            try:
                service.store._write_chat_markdown(cid)
            except OSError:
                logging.getLogger(__name__).exception('Answer saved in SQLite but Markdown sidecar could not be refreshed')
        with service.lock:
            for job in service.jobs.values():
                if job.get('conversation_id') == cid and job['status'] not in ('working', 'queued') and (
                        (nid and job.get('node_id') == nid) or (linked_mid and job.get('answer_id') == linked_mid)):
                    service.update_job(job, status='done', error='', partial='', detail='Đã lấy lại câu trả lời từ ChatGPT')
    return {'ok': True}
