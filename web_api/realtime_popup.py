"""Chrome Document PiP hosts the UI; Win32 controls real window transparency."""
import ctypes
import logging
import secrets
import sys
import threading
import time
from urllib.parse import urlencode

from caption import chatgpt

log = logging.getLogger(__name__)

OPEN_POPUP = '''async (url) => {
    const popup = await documentPictureInPicture.requestWindow({width: 1100, height: 760});
    window.__rtcRealtimePopup = popup;
    popup.document.documentElement.style.cssText = 'height:100%;margin:0';
    popup.document.body.style.cssText = 'height:100%;margin:0;overflow:hidden';
    const frame = popup.document.createElement('iframe');
    frame.title = 'Chat realtime';
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block';
    frame.allow = 'clipboard-read; clipboard-write; display-capture';
    frame.src = url;
    popup.document.body.append(frame);
}'''


class NativeWindows:
    PROPERTY = 'RTCRealtimePopupOwner'

    def __init__(self):
        if sys.platform != 'win32':
            raise ValueError('Popup luôn nổi hiện chỉ hỗ trợ Windows.')
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback, w.LPARAM], w.BOOL),
            'GetWindowTextW': ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            'IsWindow': ([w.HWND], w.BOOL),
            'IsWindowVisible': ([w.HWND], w.BOOL),
            'GetPropW': ([w.HWND, w.LPCWSTR], w.HANDLE),
            'SetPropW': ([w.HWND, w.LPCWSTR, w.HANDLE], w.BOOL),
            'RemovePropW': ([w.HWND, w.LPCWSTR], w.HANDLE),
            'GetWindowLongPtrW': ([w.HWND, ctypes.c_int], ctypes.c_ssize_t),
            'SetWindowLongPtrW': ([w.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            'SetLayeredWindowAttributes': ([w.HWND, w.DWORD, w.BYTE, w.DWORD], w.BOOL),
            'ShowWindow': ([w.HWND, ctypes.c_int], w.BOOL),
            'SetForegroundWindow': ([w.HWND], w.BOOL),
            'PostMessageW': ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result

    def find(self, title):
        matches = []
        @self.callback
        def visit(hwnd, _):
            text, kind = ctypes.create_unicode_buffer(2048), ctypes.create_unicode_buffer(128)
            self.api.GetWindowTextW(hwnd, text, len(text))
            self.api.GetClassNameW(hwnd, kind, len(kind))
            if (self.api.IsWindowVisible(hwnd) and kind.value == 'Chrome_WidgetWin_1'
                    and text.value == title and self.api.GetWindowLongPtrW(hwnd, -20) & 8):
                matches.append(hwnd)
            return True
        self.api.EnumWindows(visit, 0)
        return matches[0] if len(matches) == 1 else None

    def claim(self, hwnd, owner):
        if not self.api.SetPropW(hwnd, self.PROPERTY, owner):
            raise ctypes.WinError(ctypes.get_last_error())

    def valid(self, hwnd, owner):
        return bool(self.api.IsWindow(hwnd) and self.api.GetPropW(hwnd, self.PROPERTY) == owner)

    def configure(self, hwnd, opacity):
        style = self.api.GetWindowLongPtrW(hwnd, -20)
        ctypes.set_last_error(0)
        self.api.SetWindowLongPtrW(hwnd, -20, style | 0x80000)  # WS_EX_LAYERED
        if ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.SetLayeredWindowAttributes(hwnd, 0, round(255 * opacity / 100), 2):
            raise ctypes.WinError(ctypes.get_last_error())

    def focus(self, hwnd):
        self.api.ShowWindow(hwnd, 9)  # Restore if minimized.
        self.api.SetForegroundWindow(hwnd)

    def maintain(self, hwnd, opacity):
        # Chrome can replace extended styles during window initialization.
        if not self.api.GetWindowLongPtrW(hwnd, -20) & 0x80000:
            self.configure(hwnd, opacity)

    def close(self, hwnd):
        self.api.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE, only our popup window.

    def release(self, hwnd):
        self.api.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
        self.api.RemovePropW(hwnd, self.PROPERTY)


class RealtimePopups:
    def __init__(self, service):
        self.service, self.lock = service, threading.RLock()
        self.native = None
        self.windows = {}
        with service.store._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS realtime_popup_preferences (id INTEGER PRIMARY KEY CHECK(id=1), opacity INTEGER NOT NULL)')
            if 'font_size' not in {row['name'] for row in db.execute('PRAGMA table_info(realtime_popup_preferences)')}:
                db.execute('ALTER TABLE realtime_popup_preferences ADD COLUMN font_size INTEGER NOT NULL DEFAULT 16')
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.maintain, name='realtime-popup-windows', daemon=True)
        self.thread.start()

    def maintain(self):
        while not self.closed.wait(.75):
            with self.lock:
                if not self.windows:
                    continue
                for cid in list(self.windows):
                    item = self._window(cid)
                    if item:
                        try:
                            self.native.maintain(item['hwnd'], self.opacity())
                        except OSError:
                            log.exception('Could not maintain realtime popup window')

    def opacity(self):
        with self.service.store._connect() as db:
            row = db.execute('SELECT opacity FROM realtime_popup_preferences WHERE id=1').fetchone()
        return row['opacity'] if row else 100

    def font_size(self):
        with self.service.store._connect() as db:
            row = db.execute('SELECT font_size FROM realtime_popup_preferences WHERE id=1').fetchone()
        return row['font_size'] if row else 16

    def set_font_size(self, cid, value):
        self.service.conversation(cid)
        with self.lock:
            with self.service.store._connect() as db:
                db.execute('INSERT INTO realtime_popup_preferences(id,opacity,font_size) VALUES(1,100,?) ON CONFLICT(id) DO UPDATE SET font_size=excluded.font_size', (value,))
            return self.state(cid)

    def _window(self, cid):
        item = self.windows.get(cid)
        if item and self.native.valid(item['hwnd'], item['owner']):
            return item
        self.windows.pop(cid, None)
        return None

    def state(self, cid):
        conversation = self.service.conversation(cid)
        with self.lock:
            return dict(visible=bool(self._window(cid)), opacity=self.opacity(), font_size=self.font_size(), title=conversation.title)

    def open(self, cid, base_url, return_tab=''):
        self.service.conversation(cid)
        with self.lock:
            if self.native is None:
                self.native = NativeWindows()
            current = self._window(cid)
            if current:
                self.native.configure(current['hwnd'], self.opacity())
                self.native.focus(current['hwnd'])
                return self.state(cid)
            from playwright.sync_api import sync_playwright
            url = base_url.rstrip('/') + '/?' + urlencode({'realtime_popup': cid})
            marker = 'RTC-popup-' + secrets.token_hex(16)
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=10000)
                caller = None
                for context in browser.contexts:
                    for page in context.pages:
                        if not page.url.startswith(base_url.rstrip('/') + '/') or 'realtime_popup=' in page.url:
                            continue
                        try:
                            if page.evaluate('document.documentElement.dataset.rtcTabId') == return_tab:
                                caller = page
                                break
                        except Exception:
                            continue
                    if caller:
                        break
                if caller is None:
                    raise ValueError('Hãy mở popup từ tab ứng dụng chính trên Chrome 9222.')
                if not caller.evaluate("'documentPictureInPicture' in window"):
                    raise ValueError('Hãy cập nhật Chrome để dùng cửa sổ nổi Document Picture-in-Picture.')
                old_title = caller.title()
                opened = False
                try:
                    # The title is a temporary marker shared by the PiP native window.
                    caller.evaluate('(title) => document.title = title', marker)
                    caller.evaluate(OPEN_POPUP, url)
                    opened = True
                    deadline = time.monotonic() + 10
                    hwnd = None
                    while time.monotonic() < deadline:
                        hwnd = self.native.find(marker)
                        if hwnd:
                            break
                        caller.wait_for_timeout(100)
                    if not hwnd:
                        raise ValueError('Chưa xác định được cửa sổ nổi Chat realtime. Hãy thử lại.')
                    owner = secrets.randbelow(2**30) + 1
                    self.native.claim(hwnd, owner)
                    try:
                        self.native.configure(hwnd, self.opacity())
                    except Exception:
                        self.native.release(hwnd)
                        raise
                    self.windows[cid] = dict(hwnd=hwnd, owner=owner)
                except Exception:
                    if opened:
                        caller.evaluate('window.__rtcRealtimePopup?.close()')
                    raise
                finally:
                    if not caller.is_closed():
                        caller.evaluate('(title) => document.title = title', old_title)
            return self.state(cid)

    def set_opacity(self, cid, value):
        with self.lock:
            if not self._window(cid):
                raise ValueError('Popup chưa kết nối với backend. Hãy mở lại từ tab Chat realtime.')
            for key in list(self.windows):
                item = self._window(key)
                if item:
                    self.native.configure(item['hwnd'], value)
            with self.service.store._connect() as db:
                db.execute('INSERT INTO realtime_popup_preferences(id,opacity) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET opacity=excluded.opacity', (value,))
            return self.state(cid)

    def hide(self, cid):
        with self.lock:
            item = self._window(cid)
            if item:
                self.native.close(item['hwnd'])
            self.windows.pop(cid, None)

    def close(self):
        self.closed.set()
        with self.lock:
            for cid in list(self.windows):
                item = self._window(cid)
                if item:
                    # Backend shutdown must not discard a draft in the popup.
                    self.native.release(item['hwnd'])
            self.windows.clear()
        self.thread.join(timeout=2)
