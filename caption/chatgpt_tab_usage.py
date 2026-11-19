"""Local Free request limit, shared by app processes using the same Chrome tab."""
from contextlib import closing, contextmanager
import sqlite3

from . import paths


FREE_REQUEST_LIMIT = 10


@contextmanager
def _db():
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(paths.CACHE_DIR / 'chatgpt-tab-usage.sqlite3', timeout=5)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS free_tab_usage (
            tab_key TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used >= 0)
        )''')
        yield db


def used_requests(tab_key):
    with _db() as db:
        value = db.execute('SELECT used FROM free_tab_usage WHERE tab_key=?', (tab_key,)).fetchone()
        return value[0] if value else 0


def reserve_request(tab_key):
    """Consume one slot durably before dispatch. False means no slot was taken."""
    with _db() as db:
        result = db.execute('''INSERT INTO free_tab_usage (tab_key,used) VALUES (?,1)
            ON CONFLICT(tab_key) DO UPDATE SET used=used+1 WHERE used < ?''', (tab_key, FREE_REQUEST_LIMIT))
        return result.rowcount == 1
