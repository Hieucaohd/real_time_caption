"""Durable, process-shared spacing for opening ChatGPT tabs (never a CAPTCHA retry)."""
from contextlib import contextmanager
import math
import json
import logging
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from . import paths
from .shared_access import BusyError, ProcessLock

MIN_INTERVAL = 15.0
log = logging.getLogger(__name__)


def is_chatgpt_url(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        return parsed.scheme in ('http', 'https') and (host == 'chatgpt.com' or host.endswith('.chatgpt.com'))
    except ValueError:
        return False


@contextmanager
def _db():
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(paths.CACHE_DIR / 'chatgpt-tab-open.sqlite3', timeout=10)
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS last_open (
                id INTEGER PRIMARY KEY CHECK(id=1), opened_at REAL NOT NULL,
                target_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS seen_tabs (target_id TEXT PRIMARY KEY);
        ''')
        yield db
        db.commit()
    finally:
        db.close()


def _mark(target_id='', url='https://chatgpt.com/'):
    with _db() as db:
        db.execute('INSERT OR REPLACE INTO last_open VALUES(1,?,?,?)', (time.time(), target_id, url))
        if target_id:
            db.execute('INSERT OR IGNORE INTO seen_tabs VALUES(?)', (target_id,))


def _observe(browser):
    """Notice new/manual ChatGPT targets, without reading any conversation text."""
    if browser is None:
        return
    session = browser.new_browser_cdp_session()
    try:
        targets = session.send('Target.getTargets')['targetInfos']
    finally:
        session.detach()
    _observe_targets(targets)


def _observe_targets(targets):
    targets = [t for t in targets if t.get('type') == 'page' and is_chatgpt_url(t.get('url', ''))]
    with _db() as db:
        seen = {row[0] for row in db.execute('SELECT target_id FROM seen_tabs')}
        new = [t for t in targets if t['targetId'] not in seen]
        if new:
            db.executemany('INSERT OR IGNORE INTO seen_tabs VALUES(?)', [(t['targetId'],) for t in new])
            latest = new[-1]
            db.execute('INSERT OR REPLACE INTO last_open VALUES(1,?,?,?)',
                       (time.time(), latest['targetId'], latest['url']))


class TabObserver:
    """Observe manual tab openings in connected Chrome, including while app is idle."""
    def __init__(self):
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.run, name='chatgpt-tab-observer', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)

    def poll(self):
        # Use metadata only, no page JS or conversation content. Hold the opening
        # lock so our own about:blank -> ChatGPT transition is not counted twice.
        with ProcessLock(paths.CACHE_DIR / 'chatgpt-tab-open.lock'):
            with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=2) as response:
                tabs = json.load(response)
            _observe_targets([{**t, 'targetId': t['id']} for t in tabs if t.get('id')])

    def run(self):
        while not self.closed.is_set():
            try:
                self.poll()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass  # Chrome is closed, starting up, or an opening already owns the lock.
            except sqlite3.Error as exc:
                log.warning('Could not record ChatGPT tab timestamp (%s)', type(exc).__name__)
            self.closed.wait(1)


def _remaining():
    with _db() as db:
        row = db.execute('SELECT opened_at FROM last_open WHERE id=1').fetchone()
    # A backward wall-clock adjustment must not create an unbounded wait.
    return max(0, min(MIN_INTERVAL, MIN_INTERVAL - (time.time() - row[0]))) if row else 0


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        from .chatgpt import ChatGPTCancelled, CANCELLED_MESSAGE
        raise ChatGPTCancelled(CANCELLED_MESSAGE)


def _wait(seconds, browser, cancel):
    _check_cancel(cancel)
    # Pump Playwright events while waiting; time.sleep would stall page events.
    pages = [page for context in browser.contexts for page in context.pages] if browser else []
    if pages:
        pages[0].wait_for_timeout(seconds * 1000)
    elif cancel is not None:
        cancel.wait(seconds)
    else:
        time.sleep(seconds)
    _check_cancel(cancel)


class Opening:
    def __init__(self):
        self.target_id = ''
        self.url = 'https://chatgpt.com/'

    def opened(self, page):
        session = page.context.new_cdp_session(page)
        try:
            self.target_id = session.send('Target.getTargetInfo')['targetInfo']['targetId']
            if is_chatgpt_url(page.url):
                self.url = page.url
        finally:
            session.detach()


@contextmanager
def opening(browser=None, cancel=None, on_status=None):
    """Reserve one opening; no SQLite transaction is held during the cooldown."""
    lock = ProcessLock(paths.CACHE_DIR / 'chatgpt-tab-open.lock')
    while True:
        _check_cancel(cancel)
        try:
            lock.acquire()
            break
        except BusyError:
            _wait(.25, browser, cancel)
    try:
        deadline = None
        previous = None
        while True:
            _check_cancel(cancel)
            _observe(browser)
            with _db() as db:
                row = db.execute('SELECT opened_at FROM last_open WHERE id=1').fetchone()
            stamp = row[0] if row else None
            if stamp != previous or deadline is None:
                deadline = time.monotonic() + _remaining()
                previous = stamp
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if on_status:
                on_status(f'Đang chờ {math.ceil(remaining)} giây trước khi mở tab ChatGPT tiếp theo…')
            _wait(min(1, remaining), browser, cancel)
        _check_cancel(cancel)
        ticket = Opening()
        # Reserve before the side effect as well, so a process crash cannot lose
        # the timestamp of an already-opened browser target.
        _mark()
        try:
            yield ticket
        finally:
            _mark(ticket.target_id, ticket.url)
    finally:
        lock.release()
