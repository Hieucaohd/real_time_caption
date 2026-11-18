"""FIFO coordination for browser actions shared by the desktop and web app."""
from contextlib import closing, contextmanager
from functools import wraps
from inspect import signature
import math
import os
import sqlite3
import time

from . import paths
from .shared_access import process_alive


COOLDOWN = 10.0
POLL_INTERVAL = .25


@contextmanager
def action(cancel_event=None, on_status=None):
    """Keep our place in line until the action returns, raises, or is cancelled."""
    from .chatgpt import _check_cancel

    _check_cancel(cancel_event)
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(paths.CACHE_DIR / 'chatgpt-actions.sqlite3', timeout=5)) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER NOT NULL,
                started INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS last_action (
                id INTEGER PRIMARY KEY CHECK(id=1), finished_at REAL NOT NULL
            );
        ''')
        pid = os.getpid()
        with db:
            ticket = db.execute('INSERT INTO queue (pid) VALUES (?)', (pid,)).lastrowid
        started = False
        deadline = None
        last_status = ''
        try:
            while True:
                _check_cancel(cancel_event)
                head, owner = db.execute('SELECT id,pid FROM queue ORDER BY id LIMIT 1').fetchone()
                wait = POLL_INTERVAL
                if head == ticket:
                    if deadline is None:
                        last = db.execute('SELECT finished_at FROM last_action WHERE id=1').fetchone()
                        remaining = max(0, min(COOLDOWN, last[0] + COOLDOWN - time.time())) if last else 0
                        deadline = time.monotonic() + remaining
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _check_cancel(cancel_event)
                        with db:
                            db.execute('UPDATE queue SET started=1 WHERE id=?', (ticket,))
                        started = True
                        yield
                        return
                    status = f'Đang chờ {math.ceil(remaining)} giây trước khi thao tác với ChatGPT…'
                    wait = min(wait, remaining)
                else:
                    if owner != pid and not process_alive(owner):
                        with db:
                            removed = db.execute('DELETE FROM queue WHERE id=? RETURNING started', (head,)).fetchone()
                            if removed and removed[0]:
                                _record_finish(db)
                        continue
                    status = 'Đang xếp hàng chờ tác vụ ChatGPT trước đó…'
                if on_status and status != last_status:
                    on_status(status)
                    last_status = status
                if cancel_event is None:
                    time.sleep(wait)
                else:
                    cancel_event.wait(wait)
        finally:
            with db:
                if started:
                    _record_finish(db)
                db.execute('DELETE FROM queue WHERE id=?', (ticket,))


def _record_finish(db):
    db.execute('INSERT OR REPLACE INTO last_action VALUES (1, ?)', (time.time(),))


def queued_action(function):
    """Queue a public ChatGPT operation, preserving any caller's Stop and status hooks."""
    parameters = signature(function)

    @wraps(function)
    def run(*args, **kwargs):
        arguments = parameters.bind(*args, **kwargs).arguments
        with action(arguments.get('cancel_event'), arguments.get('on_status')):
            return function(*args, **kwargs)

    return run
