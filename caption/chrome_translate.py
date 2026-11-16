"""Use Chrome's on-device Translator in an existing local app tab over CDP.

The Settings click initializes/downloads models. This adapter never opens tabs,
changes focus, uses the clipboard, or falls back to a paid cloud provider.
"""
from __future__ import annotations

import time
import uuid
from urllib.parse import urlsplit

from .chatgpt import CDP_URL
from .translation import TranslationError, _chunks

_ERRORS = {
    "unsupported": "Chrome chưa hỗ trợ Translator API. Mở app trong Chrome desktop mới qua launch_chrome.bat.",
    "languages": "Chọn ngôn ngữ nguồn và đích khác nhau.",
    "unavailable": "Chrome Translator không hỗ trợ cặp ngôn ngữ này trên thiết bị.",
    "setup": "Mở Settings > Translation > Chrome Translator API và bấm Tải / khởi tạo gói ngôn ngữ, sau đó Retry.",
    "preparing": "Chrome đang tải/khởi tạo gói ngôn ngữ. Chờ trạng thái Sẵn sàng rồi Retry.",
    "prepare": "Không khởi tạo được Chrome Translator. Mở Settings để khởi tạo lại gói ngôn ngữ.",
    "empty": "Chrome Translator trả về bản dịch trống. Hãy thử lại.",
    "cancelled": "Đã dừng dịch Chrome Translator.",
    "translate": "Chrome Translator không dịch được. Kiểm tra gói ngôn ngữ trong Settings rồi Retry.",
}


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TranslationError(_ERRORS["cancelled"])


class AppTabUnavailable(TranslationError):
    pass


def _choose_tab(browser, source, target):
    # contexts/pages and page.url are cached by Playwright. A worker can sleep
    # while Chrome replaces a tab; pump CDP events before reading that cache.
    session = browser.new_browser_cdp_session()
    try:
        session.send('Target.getTargets')
    finally:
        session.detach()
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            url = urlsplit(page.url)
            if url.scheme not in ("http", "https") or url.hostname not in ("127.0.0.1", "localhost", "::1"):
                continue
            try:
                info = page.evaluate("""([source, target]) => {
                    const bridge = window.__rtcChromeTranslator;
                    if (!document.documentElement.dataset.rtcTabId || bridge?.version !== 1) return null;
                    return {ready: bridge.has(source, target), focused: document.hasFocus()};
                }""", [source, target])
                if info:
                    candidates.append(((bool(info["ready"]), bool(info["focused"])), page))
            except Exception:
                continue
    if not candidates:
        raise AppTabUnavailable("Mở tab ứng dụng trong Chrome qua launch_chrome.bat, vào Settings và khởi tạo Chrome Translator API. Nếu app đã mở, hãy F5.")
    return max(candidates, key=lambda item: item[0])[1]


def _translate_page(page, text, source, target, cancel=None, timeout_s=30):
    job_id = uuid.uuid4().hex
    _check_cancel(cancel)
    try:
        page.evaluate("job => window.__rtcChromeTranslator.start(job)",
                      {"id": job_id, "text": text, "source": source, "target": target})
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            _check_cancel(cancel)
            result = page.evaluate("id => window.__rtcChromeTranslator?.read(id)", job_id)
            if not result:
                raise TranslationError("Tab ứng dụng đã tải lại hoặc đóng trong lúc dịch. Hãy Retry.")
            if result["status"] == "done":
                _check_cancel(cancel)
                if not isinstance(result.get("text"), str) or not result["text"].strip():
                    raise TranslationError(_ERRORS["empty"])
                return result["text"].strip()
            if result["status"] == "error":
                raise TranslationError(_ERRORS.get(result.get("code"), _ERRORS["translate"]))
            if cancel is None:
                time.sleep(.05)
            else:
                cancel.wait(.05)
        raise TranslationError("Chrome Translator chưa phản hồi sau 30 giây. Kiểm tra tab ứng dụng rồi Retry.")
    finally:
        try:
            page.evaluate("id => window.__rtcChromeTranslator?.release(id)", job_id)
        except Exception:
            pass  # Reload/close also destroys the document's model and requests.


class Client:
    """A CDP connection owned and reused exclusively by one translation thread."""
    def __init__(self):
        self.playwright = self.browser = None

    def close(self):
        # Stop the driver to disconnect CDP; never call browser.close().
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.playwright = self.browser = None

    def connect(self):
        self.browser = self.playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        for context in self.browser.contexts:
            # Playwright otherwise auto-dismisses native dialogs on every tab.
            # Multiple translator lanes race to dismiss the same user prompt,
            # which can also crash their drivers. Leave all dialogs to the user.
            context.on('dialog', lambda dialog: None)

    def translate(self, text, source, target, cancel=None):
        from playwright.sync_api import sync_playwright

        _check_cancel(cancel)
        if not text:
            raise TranslationError("Nhập nội dung cần dịch.")
        try:
            if self.browser is None or not self.browser.is_connected():
                self.close()
                try:
                    self.playwright = sync_playwright().start()
                    self.connect()
                except Exception:
                    self.close()
                    raise TranslationError("Không kết nối được Chrome 9222. Chạy launch_chrome.bat rồi thử lại.") from None
            try:
                page = _choose_tab(self.browser, source, target)
            except AppTabUnavailable:
                # Re-enumerate from a fresh connection before declaring the app
                # missing. No translation has been submitted, so this is safe.
                self.close()
                _check_cancel(cancel)
                self.playwright = sync_playwright().start()
                self.connect()
                deadline = time.monotonic() + 3
                while True:
                    _check_cancel(cancel)
                    try:
                        page = _choose_tab(self.browser, source, target)
                        break
                    except AppTabUnavailable:
                        if time.monotonic() >= deadline:
                            raise
                        if cancel is None:
                            time.sleep(.1)
                        else:
                            cancel.wait(.1)
            return " ".join(_translate_page(page, chunk, source, target, cancel) for chunk in _chunks(text))
        except TranslationError:
            raise
        except Exception:
            self.close()
            raise TranslationError("Mất kết nối với tab ứng dụng khi dịch. Mở lại app trong Chrome và Retry.") from None


def translate(text, source, target, cancel=None):
    client = Client()
    try:
        return client.translate(text, source, target, cancel)
    finally:
        client.close()
