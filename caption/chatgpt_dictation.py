"""Read live microphone text from one reserved ChatGPT composer; never send it."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid

from . import chatgpt, paths
from .dictation_text import DictationText
from .shared_access import ProcessLock
from .transcriber import Event

log = logging.getLogger(__name__)
LABEL = 'ChatGPT Dictation (microphone)'
START = re.compile(r'^(Dictate|Dictation|Use microphone|Đọc chính tả|Nhập bằng giọng nói|Đọc bằng giọng nói)$', re.I)
STOP = re.compile(r'^(Stop dictation|Stop recording|Finish dictation|Stop|Dừng|Dừng ghi âm|Dừng đọc chính tả)$', re.I)
CANCEL = re.compile(r'^(Cancel dictation|Cancel recording|Cancel|Hủy|Hủy ghi âm)$', re.I)
POLL_SECONDS = .2
START_READY_TIMEOUT = 30
EMPTY_COMPOSER_SECONDS = 1.0
START_NOT_READY = ('Nút Dictate của ChatGPT chưa sẵn sàng sau 30 giây. '
                   'Hãy kiểm tra trang ChatGPT đã tải xong và cho phép dùng micro trong Chrome, rồi bấm Start lại.')


class Interrupted(Exception):
    pass


def validate_source(source):
    if source not in ('windows', 'chatgpt'):
        raise ValueError('Nguồn transcript phải là Windows Live Captions hoặc ChatGPT.')


class ChatGPTDictationReader:
    def __init__(self, on_event, return_tab=''):
        self._emit, self.return_tab = on_event, return_tab
        self._stop, self._abort = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name='chatgpt-dictation', daemon=True)
        self.page = None
        self.tab_key = ''
        self.recording = False
        self.text = None
        self.partial = None
        self.level = 0.0

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def shutdown(self):
        self._stop.set()
        self._abort.set()

    def _check(self):
        if self._abort.is_set() or self._stop.is_set():
            raise Interrupted()

    def _wait(self, page, predicate, timeout, message):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check()
            if page.is_closed():
                raise chatgpt.ChatGPTError('Tab thu âm ChatGPT đã bị đóng. Bấm Start để bắt đầu lại.')
            if predicate():
                return
            page.wait_for_timeout(100)
        raise chatgpt.ChatGPTError(message)

    @staticmethod
    def _button(page, name):
        return page.get_by_role('button', name=name).filter(visible=True).locator(
            'xpath=self::*[not(@data-testid="send-button") and not(@id="composer-submit-button")]').first

    @staticmethod
    def _text(composer):
        if not composer.count():
            return ''
        return composer.evaluate("el => (el.value ?? el.innerText ?? '').trim()")

    def _open_page(self, browser):
        self._check()
        with ProcessLock(paths.CACHE_DIR / 'chatgpt-request.lock', timeout=2):
            self.page = page = chatgpt._open_background_tab(browser)
            key = chatgpt.DICTATION_TAB_PREFIX + uuid.uuid4().hex
            self.tab_key = key
            page.add_init_script('window.name = ' + json.dumps(key) + ';')
            page.evaluate('(key) => window.name = key', key)
        page.set_default_timeout(2000)
        from playwright.sync_api import TimeoutError
        try:
            page.goto('https://chatgpt.com/', wait_until='commit', timeout=2000)
        except TimeoutError:
            pass
        chatgpt.require_page_login(page, cancel_event=self._stop)
        self._wait(page, lambda: self._button(page, START).count(), 30,
                   'Không tìm thấy nút Dictate. Kiểm tra đăng nhập ChatGPT và kết nối trong Chrome 9222.')
        page.evaluate("document.title = '🎙 Transcript realtime · ChatGPT'")
        return page

    def _wait_start_ready(self, page):
        from playwright.sync_api import TimeoutError

        empty_since = None

        def ready():
            nonlocal empty_since
            button = self._button(page, START)
            composer = chatgpt._visible(page, chatgpt.COMPOSER)
            if not button.count() or not button.is_enabled() or not composer.count() or not composer.is_editable():
                empty_since = None
                return False
            try:
                button.click(trial=True, timeout=300)
                # ChatGPT may hydrate an old home-page draft after the composer
                # first appears. Clear through the editor's input event, then
                # require a quiet empty period before starting the microphone.
                if self._text(composer):
                    composer.fill('', timeout=1000)
                    empty_since = None
                    return False
            except TimeoutError:
                empty_since = None
                return False
            empty_since = empty_since or time.monotonic()
            return time.monotonic() - empty_since >= EMPTY_COMPOSER_SECONDS

        self._emit(Event('status', 'Đang chờ nút Dictate sẵn sàng trên ChatGPT…'))
        self._wait(page, ready, START_READY_TIMEOUT, START_NOT_READY)

    def _begin(self, page, browser):
        if page is not self.page or not self.tab_key or page.evaluate('window.name') != self.tab_key:
            raise chatgpt.ChatGPTError('Không xác nhận được tab thu âm riêng. Bấm Start để mở lại tab thu âm.')
        with chatgpt._return_to_web_tab(browser, self.return_tab):
            page.bring_to_front()
            self._wait_start_ready(page)
            self._check()
            chatgpt.require_page_login(page, cancel_event=self._stop)
            self.text = DictationText()
            self._emit(Event('status', 'Đang bật micro ChatGPT… Cho phép micro trong Chrome nếu được hỏi.'))
            from playwright.sync_api import TimeoutError
            try:
                self._button(page, START).click(timeout=2000)
            except TimeoutError as exc:
                raise chatgpt.ChatGPTError('Nút Dictate thay đổi hoặc chưa bấm được. Hãy chờ ChatGPT tải xong rồi bấm Start lại.') from exc
            self.recording = True
            self._wait(page, lambda: self._button(page, CANCEL).count() or self._button(page, STOP).count(), 20,
                       'ChatGPT chưa bắt đầu ghi âm. Kiểm tra quyền micro và nút Dictate trong Chrome.')
        self._emit(Event('ready', 'ChatGPT đang nghe · đọc văn bản realtime'))

    def _read_live(self, *, flush=False):
        if not self.text:
            return
        snapshot = ''
        if self.page and not self.page.is_closed():
            snapshot = self._text(chatgpt._visible(self.page, chatgpt.COMPOSER))
        completed, partial = self.text.update(snapshot, time.monotonic(), flush=flush)
        for text in completed:
            if self._emit(Event('final', text, end_of_utterance=True)) is False:
                raise chatgpt.ChatGPTError('Không lưu được transcript vào database; thu âm đã dừng.')
        if partial != self.partial:
            self.partial = partial
            self._emit(Event('partial', partial))

    def _monitor(self):
        stopped_since = None
        while not self._stop.is_set():
            self._check()
            if self.page.is_closed():
                raise chatgpt.ChatGPTError('Tab thu âm ChatGPT đã bị đóng. Phần đã đọc được giữ lại; bấm Start để tiếp tục.')
            self._read_live()
            start = self._button(self.page, START)
            if start.count() and start.is_enabled() and not self._button(self.page, CANCEL).count():
                stopped_since = stopped_since or time.monotonic()
                if time.monotonic() - stopped_since > 2:
                    self.recording = False
                    raise chatgpt.ChatGPTError('ChatGPT đã dừng nghe. Phần đã đọc được giữ lại; bấm Start để tiếp tục.')
            else:
                stopped_since = None
            self.page.wait_for_timeout(POLL_SECONDS * 1000)

    def _capture(self, browser):
        page = self._open_page(browser)
        self._begin(page, browser)
        self._monitor()

    def _cleanup(self):
        if not self.page:
            return
        try:
            if self.recording and not self.page.is_closed():
                button = self._button(self.page, STOP)
                if button.count() and button.is_enabled():
                    button.click(timeout=1000)  # Only on Pause/exit, never on a timer.
                    deadline = time.monotonic() + 2
                    while not self._abort.is_set() and time.monotonic() < deadline:
                        self._read_live()
                        if not self._button(self.page, CANCEL).count():
                            break
                        self.page.wait_for_timeout(100)
        finally:
            try:
                self._read_live(flush=True)
                # Only after the final words have been accepted by the app:
                # remove the recording draft so the next tab cannot restore it.
                # Never clear a live microphone or another ChatGPT tab.
                if self.text and not self.page.is_closed():
                    try:
                        start = self._button(self.page, START)
                        if (self.page.evaluate('window.name') == self.tab_key
                                and start.count() and start.is_enabled()
                                and not self._button(self.page, CANCEL).count()):
                            composer = chatgpt._visible(self.page, chatgpt.COMPOSER)
                            if composer.count() and composer.is_editable():
                                composer.fill('', timeout=1000)
                                self.page.wait_for_timeout(250)
                    except Exception:
                        log.debug('Could not clear the finished dictation draft', exc_info=True)
            finally:
                try:
                    self.page.close(run_before_unload=False)
                except Exception:
                    log.debug('Dictation tab already closed', exc_info=True)
                self.page = None
                self.recording = False

    def _run(self):
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                try:
                    try:
                        browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=5000)
                    except Exception as exc:
                        raise chatgpt.ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
                    if not browser.contexts:
                        raise chatgpt.ChatGPTError('Chrome chưa có cửa sổ. Hãy chạy launch_chrome.bat.')
                    self._capture(browser)
                except (Interrupted, chatgpt.ChatGPTCancelled):
                    pass
                finally:
                    self._cleanup()
        except chatgpt.ChatGPTLoginRequired as exc:
            self._emit(Event('login_required', str(exc)))
        except Exception as exc:
            log.exception('ChatGPT dictation stopped')
            message = str(exc) if isinstance(exc, chatgpt.ChatGPTError) else (
                'Không thao tác được với tab thu âm. Kiểm tra ChatGPT đã tải xong, quyền micro và kết nối, rồi bấm Start lại. '
                'Chi tiết kỹ thuật được lưu trong caption-web.log.')
            self._emit(Event('error', f'Không lấy được transcript ChatGPT: {message}'))
        finally:
            self._emit(Event('stopped'))
