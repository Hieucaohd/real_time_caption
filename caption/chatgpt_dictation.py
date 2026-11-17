"""Capture microphone dictation through ChatGPT's UI, without sending a chat message.

Only tabs created by this reader are used/closed. At most two exist: the current
recording and its successor (or the previous recording being transcribed).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass

from . import chatgpt, paths
from .shared_access import ProcessLock
from .transcriber import Event

log = logging.getLogger(__name__)
LABEL = 'ChatGPT Dictation (microphone)'
START = re.compile(r'^(Dictate|Dictation|Use microphone|Đọc chính tả|Nhập bằng giọng nói|Đọc bằng giọng nói)$', re.I)
FINISH = re.compile(r'^(Submit dictation|Stop dictation|Finish dictation|Submit recording|Stop recording|Submit|Confirm|Dừng ghi âm|Gửi bản ghi|Xác nhận|Gửi)$', re.I)
CANCEL = re.compile(r'^(Cancel dictation|Cancel recording|Cancel|Hủy|Hủy ghi âm)$', re.I)
START_READY_TIMEOUT = 30
START_NOT_READY = ('Nút Dictate của ChatGPT chưa sẵn sàng sau 30 giây. '
                   'Hãy kiểm tra trang ChatGPT đã tải xong và cho phép dùng micro trong Chrome, rồi bấm Start lại.')


class Interrupted(Exception):
    pass


def validate_options(source, interval):
    if source not in ('windows', 'chatgpt'):
        raise ValueError('Nguồn transcript phải là Windows Live Captions hoặc ChatGPT.')
    if type(interval) is not int or not 5 <= interval <= 120:
        raise ValueError('Khoảng lấy transcript ChatGPT phải từ 5 đến 120 giây.')


@dataclass
class Segment:
    page: object
    started: float | None = None
    submitted: bool = False
    saved: bool = False


class ChatGPTDictationReader:
    def __init__(self, on_event, interval=15, overlap=True, return_tab=''):
        validate_options('chatgpt', interval)
        self._emit, self.interval, self.overlap, self.return_tab = on_event, interval, overlap, return_tab
        self._stop, self._abort = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._run, name='chatgpt-dictation', daemon=True)
        self.segments = []
        self.level = 0.0

    def start(self):
        self._thread.start()

    def stop(self):
        # Pause finishes the current recording so its last words are retained.
        self._stop.set()

    def shutdown(self):
        self._stop.set()
        self._abort.set()

    def _check(self, allow_stop=False):
        if self._abort.is_set() or (self._stop.is_set() and not allow_stop):
            raise Interrupted()

    def _wait(self, page, predicate, timeout, message, allow_stop=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check(allow_stop)
            if page.is_closed():
                raise chatgpt.ChatGPTError('Tab thu âm ChatGPT đã bị đóng. Bấm Start để bắt đầu lại.')
            result = predicate()
            if result:
                return result
            page.wait_for_timeout(100)
        raise chatgpt.ChatGPTError(message)

    @staticmethod
    def _button(page, name):
        # Never fall back to the chat Send button or the live Voice conversation.
        return page.get_by_role('button', name=name).filter(visible=True).locator(
            'xpath=self::*[not(@data-testid="send-button") and not(@id="composer-submit-button")]').first

    def _new_segment(self, browser):
        self._check()
        # Tag before navigation. Scans must never assign ordinary-chat keys to it.
        with ProcessLock(paths.CACHE_DIR / 'chatgpt-request.lock', timeout=2):
            page = chatgpt._open_background_tab(browser)
            segment = Segment(page)
            self.segments.append(segment)
            key = chatgpt.DICTATION_TAB_PREFIX + uuid.uuid4().hex
            page.add_init_script('window.name = ' + json.dumps(key) + ';')
            page.evaluate('(key) => window.name = key', key)
        page.set_default_timeout(2000)
        # Do not wait for all ChatGPT resources; keep Pause responsive on slow loads.
        try:
            page.goto('https://chatgpt.com/', wait_until='commit', timeout=2000)
        except Exception as exc:
            from playwright.sync_api import TimeoutError
            if not isinstance(exc, TimeoutError):
                raise
        self._wait(page, lambda: self._button(page, START).count(), 30,
                   'Không tìm thấy nút Dictate. Kiểm tra đăng nhập ChatGPT và kết nối trong Chrome 9222.')
        page.evaluate("document.title = '🎙 Transcript · ChatGPT'")
        return segment

    @staticmethod
    def _text(composer):
        if not composer.count():
            return ''
        return composer.evaluate("el => (el.value ?? el.innerText ?? '').trim()")

    def _wait_start_ready(self, page):
        from playwright.sync_api import TimeoutError

        def ready():
            button = self._button(page, START)
            composer = chatgpt._visible(page, chatgpt.COMPOSER)
            if not button.count() or not button.is_enabled() or not composer.count() or not composer.is_editable():
                return False
            try:
                # A visible/enabled button can still be moving during hydration or
                # covered by a loading overlay. Trial never starts the microphone.
                button.click(trial=True, timeout=300)
            except TimeoutError:
                return False
            return True

        self._emit(Event('status', 'Đang chờ nút Dictate sẵn sàng trên ChatGPT…'))
        self._wait(page, ready, START_READY_TIMEOUT, START_NOT_READY)

    def _begin(self, segment, browser, first=False):
        self._check()
        page = segment.page
        # First start brings the permission prompt into view, then restores the app.
        # Successor tabs remain in the background, without repeatedly stealing focus.
        with chatgpt._return_to_web_tab(browser, self.return_tab) if first else nullcontext():
            if first:
                page.bring_to_front()
            self._wait_start_ready(page)
            self._check()
            self._emit(Event('status', 'Đang bật micro ChatGPT… Cho phép micro trong Chrome nếu được hỏi.'))
            try:
                self._button(page, START).click(timeout=2000)
            except Exception as exc:
                from playwright.sync_api import TimeoutError
                if not isinstance(exc, TimeoutError):
                    raise
                # Do not force-click or repeat a real click: recording might have
                # begun just as the DOM changed. Cleanup closes our owned tab.
                raise chatgpt.ChatGPTError('Nút Dictate thay đổi hoặc chưa bấm được. Hãy chờ ChatGPT tải xong rồi bấm Start lại.') from exc
            segment.started = time.monotonic()
            self._wait(page, lambda: self._button(page, CANCEL).count() and self._button(page, FINISH).count(), 20,
                       'ChatGPT chưa bắt đầu ghi âm. Kiểm tra quyền micro và nút Dictate trong Chrome.')
        self._emit(Event('ready', f'ChatGPT đang nghe micro · mỗi {self.interval} giây'))

    def _finish(self, segment):
        if segment.saved or segment.started is None:
            return
        self._check(allow_stop=True)
        page = segment.page
        if not segment.submitted:
            if self._button(page, CANCEL).count():
                self._button(page, FINISH).click(timeout=2000)
            elif not self._button(page, START).count():
                raise chatgpt.ChatGPTError('Không thấy điều khiển kết thúc Dictate; app không bấm nút gửi câu hỏi.')
            # ChatGPT can also end a recording itself. In that case just read
            # the returned editor instead of clicking any ordinary Submit button.
            segment.submitted = True
        self._emit(Event('status', 'Đang lấy văn bản ChatGPT…' + (' Tab kế tiếp vẫn nghe.' if any(
            s is not segment and s.started is not None for s in self.segments) else '')))
        # Dictate returning means the upload/transcription phase has finished.
        # Wait for the editor to settle as the text insertion can follow that button.
        last, stable_since = None, time.monotonic()
        def completed():
            nonlocal last, stable_since
            start = self._button(page, START)
            if not start.count() or not start.is_enabled() or self._button(page, CANCEL).count():
                stable_since = time.monotonic()
                return False
            text = self._text(chatgpt._visible(page, chatgpt.COMPOSER))
            if text != last:
                last, stable_since = text, time.monotonic()
            if time.monotonic() - stable_since < (0.5 if text else 2):
                return False
            return (text,)
        text, = self._wait(page, completed, 45,
                           'ChatGPT chưa trả transcript sau 45 giây. Thu âm đã dừng; kiểm tra kết nối rồi thử lại.', allow_stop=True)
        if text:
            self._emit(Event('warning', ''))
            if self._emit(Event('final', text, end_of_utterance=True)) is False:
                raise chatgpt.ChatGPTError('Không lưu được transcript vào database; thu âm đã dừng.')
            # Clear only our already-saved text so ChatGPT does not restore it as
            # a draft in the next fresh tab. Never clear a user's changed draft.
            composer = chatgpt._visible(page, chatgpt.COMPOSER)
            if self._text(composer) == text:
                composer.fill('', timeout=2000)
        else:
            self._emit(Event('warning', 'Đoạn vừa ghi không có văn bản; ChatGPT có thể không nghe thấy giọng nói.'))
        segment.saved = True

    def _close_segment(self, segment):
        try:
            segment.page.close(run_before_unload=False)
        except Exception:
            log.debug('Dictation tab already closed', exc_info=True)
        self.segments.remove(segment)

    def _capture(self, browser):
        current = self._new_segment(browser)
        self._begin(current, browser, first=True)
        while True:
            successor = None
            if self.overlap and not self._stop.is_set():
                successor = self._new_segment(browser)
            while time.monotonic() - current.started < self.interval and not self._stop.is_set():
                self._check()
                current.page.wait_for_timeout(100)
            if successor and not self._stop.is_set():
                # Confirm successor is listening before submitting the older recording.
                self._begin(successor, browser)
            self._finish(current)
            self._close_segment(current)
            if self._stop.is_set():
                break
            current = successor or self._new_segment(browser)
            if current.started is None:
                self._begin(current, browser)
            else:
                self._emit(Event('status', f'ChatGPT đang nghe micro · mỗi {self.interval} giây'))

    def _cleanup(self):
        for segment in list(self.segments):
            try:
                if not self._abort.is_set() and not segment.submitted:
                    self._finish(segment)
            except Interrupted:
                pass
            except Exception:
                self._emit(Event('warning', 'Không lấy được phần cuối đoạn ghi âm từ ChatGPT.'))
            finally:
                self._close_segment(segment)

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
                except Interrupted:
                    pass
                finally:
                    # Flush tail on Pause, including a successor already recording.
                    # Shutdown skips upload waits but always closes the owned tabs.
                    self._cleanup()
        except Exception as exc:
            log.exception('ChatGPT dictation stopped')
            message = str(exc) if isinstance(exc, chatgpt.ChatGPTError) else (
                'Không thao tác được với tab thu âm. Kiểm tra ChatGPT đã tải xong, quyền micro và kết nối, rồi bấm Start lại. '
                'Chi tiết kỹ thuật được lưu trong caption-web.log.')
            self._emit(Event('error', f'Không lấy được transcript ChatGPT: {message}'))
        finally:
            self._emit(Event('stopped'))
