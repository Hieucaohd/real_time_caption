"""Translate through an existing Google Translate tab in the app's CDP Chrome.

Only the Translate UI is used: no undocumented translation HTTP endpoint, new
browser/profile/tab, permission grants, or OS clipboard. Test and queued jobs
share a cancellable lock so they cannot overwrite each other's source text.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from .chatgpt import CDP_URL
from .translation import TranslationError, _chunks

TAB_NAME = "real-time-caption-google-translate"
_TAB_LOCK = threading.Lock()
_HOSTS = {"translate.google.com", "translate.google.com.vn"}
SOURCE = 'textarea[jsname="BJE2fc"], textarea[aria-label="Source text"]'
OUTPUT = 'span.ryNqvb'
OUTPUT_FALLBACK = 'span[jsname="W297wb"]'
COPY = 'button[aria-label="Copy translation"], button[aria-label="Sao chép bản dịch"]'

_COPY_INSTALL = """() => {
  const c = navigator.clipboard;
  if (!c) return false;
  const state = {texts: [], writeText: c.writeText, write: c.write};
  state.captureText = async text => { state.texts.push(String(text)); };
  state.capture = async items => {
    for (const item of items) if (item.types.includes('text/plain')) {
      state.texts.push(await (await item.getType('text/plain')).text());
    }
  };
  state.listener = event => {
    event.preventDefault();
    queueMicrotask(() => {
      const text = event.clipboardData?.getData('text/plain');
      if (text) state.texts.push(text);
    });
  };
  window.__rtcTranslateClipboard = state;
  c.writeText = state.captureText;
  c.write = state.capture;
  document.addEventListener('copy', state.listener, true);
  return c.writeText === state.captureText && c.write === state.capture;
}"""
_COPY_READ = "() => window.__rtcTranslateClipboard?.texts || []"
_COPY_REMOVE = """() => {
  const state = window.__rtcTranslateClipboard;
  if (!state) return;
  const c = navigator.clipboard;
  if (c?.writeText === state.captureText) c.writeText = state.writeText;
  if (c?.write === state.capture) c.write = state.write;
  document.removeEventListener('copy', state.listener, true);
  delete window.__rtcTranslateClipboard;
}"""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TranslationError("Translation cancelled.")


def _pause(cancel, seconds=0.15):
    if cancel is None:
        time.sleep(seconds)
    else:
        cancel.wait(seconds)
    _check_cancel(cancel)


def _is_translate_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in _HOSTS and parsed.path in ("", "/")


def _choose_tab(browser):
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if not _is_translate_url(page.url):
                continue
            try:
                score = page.evaluate("""() => ({
                    pinned: window.name === 'real-time-caption-google-translate',
                    focused: document.hasFocus(),
                    visible: document.visibilityState === 'visible'
                })""")
                candidates.append(((bool(score["pinned"]), bool(score["focused"]), bool(score["visible"])), page))
            except Exception:
                continue  # The tab may have closed while scanning.
    if not candidates:
        raise TranslationError("Open a Google Translate tab (translate.google.com) in the Chrome started by launch_chrome.bat, then Retry.")
    chosen = max(candidates, key=lambda item: item[0])[1]
    chosen.evaluate("name => window.name = name", TAB_NAME)
    return chosen


def _visible(page, selector):
    return page.locator(selector).filter(visible=True)


def _read_output(page):
    parts = _visible(page, OUTPUT).all_inner_texts()
    if not parts:
        parts = _visible(page, OUTPUT_FALLBACK).all_inner_texts()
    return " ".join(part.strip() for part in parts if part.strip())


def _copy_result(page, rendered, cancel):
    _check_cancel(cancel)
    try:
        installed = page.evaluate(_COPY_INSTALL)
        if not installed:
            return rendered  # Never click Copy if the OS clipboard is unprotected.
        _visible(page, COPY).first.click(timeout=1500)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            _check_cancel(cancel)
            for text in page.evaluate(_COPY_READ):
                if text.strip() and " ".join(text.split()) == " ".join(rendered.split()):
                    return text.strip()
            _pause(cancel)
    except TranslationError:
        raise
    except Exception:
        pass  # Verified rendered target text is the safe clipboard-free fallback.
    finally:
        try:
            page.evaluate(_COPY_REMOVE)
        except Exception:
            pass  # Navigation destroys the temporary hook as well.
    _check_cancel(cancel)
    return rendered


def _translate_page(page, text, source, target, cancel, timeout_s=30):
    _check_cancel(cancel)
    if not _is_translate_url(page.url):
        raise TranslationError("The Google Translate tab navigated away. Open Translate again and Retry.")
    codes = {"zh": "zh-CN"}
    source, target = codes.get(source, source), codes.get(target, target)
    params = {"sl": source, "tl": target, "op": "translate", "hl": "en"}
    parsed = urlsplit(page.url)
    current = parse_qs(parsed.query)
    clean_url = f"https://{parsed.hostname}/?{urlencode(params)}"
    if any(current.get(key, [""])[0] != value for key, value in params.items()):
        page.goto(clean_url, wait_until="domcontentloaded", timeout=10_000)
        page.evaluate("name => window.name = name", TAB_NAME)
    input_box = _visible(page, SOURCE).first
    input_box.wait_for(state="visible", timeout=10_000)
    _check_cancel(cancel)
    input_box.fill("", timeout=1500)
    # Observe an empty target after clearing before submitting anything. A new
    # answer may legitimately equal the previous answer, so text inequality alone
    # cannot distinguish fresh results from stale results.
    clear_deadline = time.monotonic() + 2
    while _read_output(page):
        _check_cancel(cancel)
        if time.monotonic() >= clear_deadline:
            page.goto(clean_url, wait_until="domcontentloaded", timeout=10_000)
            page.evaluate("name => window.name = name", TAB_NAME)
            input_box = _visible(page, SOURCE).first
            input_box.wait_for(state="visible", timeout=10_000)
            if _read_output(page) or input_box.input_value(timeout=1500):
                raise TranslationError("Google Translate did not clear its previous result. Check the tab and Retry.")
            break
        _pause(cancel)
    _check_cancel(cancel)
    if not _is_translate_url(page.url):
        raise TranslationError("The Google Translate tab navigated away. Retry after reopening it.")
    input_box.fill(text, timeout=1500)
    deadline = time.monotonic() + timeout_s
    last, changed = "", time.monotonic()
    while time.monotonic() < deadline:
        _check_cancel(cancel)
        if not _is_translate_url(page.url):
            raise TranslationError("The Google Translate tab navigated away while translating.")
        params_now = parse_qs(urlsplit(page.url).query)
        if any(params_now.get(key, [""])[0] != value for key, value in (("sl", source), ("tl", target))):
            raise TranslationError("Languages changed in the Translate tab. Retry with the desired app settings.")
        if input_box.input_value(timeout=1500) != text:
            raise TranslationError("Source text changed in the Translate tab. Retry when the tab is not being edited.")
        result = _read_output(page)
        if result != last:
            last, changed = result, time.monotonic()
        button = _visible(page, COPY).first
        if result and time.monotonic() - changed >= 0.6 and button.count() and button.is_enabled():
            copied = _copy_result(page, result, cancel)
            _check_cancel(cancel)
            if input_box.input_value(timeout=1500) != text:
                raise TranslationError("Source text changed before the translation could be read. Retry.")
            return copied
        _pause(cancel)
    raise TranslationError(f"Google Translate did not finish within {timeout_s:g} seconds. Check connection, consent/CAPTCHA or changes to the page, then Retry manually.")


def translate(text, source, target, cancel=None):
    """Serialize all chunks and disconnect without closing Chrome or its tabs."""
    from playwright.sync_api import sync_playwright

    _check_cancel(cancel)
    while not _TAB_LOCK.acquire(timeout=0.15):
        _check_cancel(cancel)
    try:
        _check_cancel(cancel)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
            except Exception:
                raise TranslationError("Could not connect to Chrome on port 9222. Start launch_chrome.bat, open Google Translate and Retry.") from None
            page = _choose_tab(browser)
            result = [_translate_page(page, chunk, source, target, cancel) for chunk in _chunks(text)]
            if not result:
                raise TranslationError("Enter some text to translate.")
            return " ".join(result)
        # Exiting Playwright disconnects CDP. browser.close() would close the
        # user's Chrome, so it must never be called here.
    except TranslationError:
        raise
    except Exception:
        # Playwright exception text can contain source text or private tab URLs.
        raise TranslationError("Could not read Google Translate. Check that its text input and translation are visible, then Retry. The page may have changed.") from None
    finally:
        _TAB_LOCK.release()
