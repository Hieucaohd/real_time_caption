"""Translate through a reused or newly opened Translate tab in the app's CDP Chrome.

Only the Translate UI is used: no undocumented translation HTTP endpoint, new
browser/profile, permission grants, or OS clipboard. Test and queued jobs
share a cancellable lock so they cannot overwrite each other's source text.
"""

from __future__ import annotations

import threading
import time
import hashlib
import re
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit

from .chatgpt import CDP_URL, _open_background_tab
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


def _translation_params(source, target):
    codes = {"zh": "zh-CN"}
    return {"sl": codes.get(source, source), "tl": codes.get(target, target), "op": "translate", "hl": "en"}


def _choose_tab(browser, source="en", target="vi", cancel=None, *, stream_key='', text=''):
    _check_cancel(cancel)
    candidates = []
    name = TAB_NAME + ':' + hashlib.sha256(stream_key.encode()).hexdigest()[:24] if stream_key else TAB_NAME
    for context in browser.contexts:
        for page in context.pages:
            if not _is_translate_url(page.url):
                continue
            try:
                if stream_key:
                    existing_name = page.evaluate('() => window.name')
                    if existing_name == name:
                        return page
                    if existing_name.startswith(TAB_NAME + ':'):
                        continue  # Other conversations keep their source text.
                    params = parse_qs(urlsplit(page.url).query)
                    wanted = _translation_params(source, target)
                    if any(params.get(k, [''])[0] != wanted[k] for k in ('sl', 'tl')):
                        continue
                    box = _visible(page, SOURCE)
                    if not box.count() or not text.startswith(box.first.input_value(timeout=1500)):
                        continue  # Never replace an unrelated paragraph.
                score = page.evaluate("""() => ({
                    pinned: window.name === 'real-time-caption-google-translate',
                    stream: window.name.startsWith('real-time-caption-google-translate:'),
                    focused: document.hasFocus(),
                    visible: document.visibilityState === 'visible'
                })""")
                if not stream_key and score.get('stream'):
                    continue
                candidates.append(((bool(score["pinned"]), bool(score["focused"]), bool(score["visible"])), page))
            except Exception:
                continue  # The tab may have closed while scanning.
    if not candidates:
        if not browser.contexts:
            raise TranslationError("Chrome has no available window. Reopen the app or launch_chrome.bat and Retry.")
        _check_cancel(cancel)
        chosen = _open_background_tab(browser)
        try:
            chosen.goto('https://translate.google.com/?' + urlencode(_translation_params(source, target)),
                        wait_until='load', timeout=30_000)
            # The source textarea is server-rendered before Google's handlers
            # initialize; typing immediately can be erased during hydration.
            _pause(cancel, 1.0)
            _check_cancel(cancel)
            chosen.evaluate("name => window.name = name", name)
            return chosen
        except Exception:
            # Only close the tab we created; leave all existing user tabs alone.
            try:
                chosen.close()
            except Exception:
                pass
            raise
    chosen = max(candidates, key=lambda item: item[0])[1]
    chosen.evaluate("name => window.name = name", name)
    return chosen


def _visible(page, selector):
    return page.locator(selector).filter(visible=True)


def _read_output(page):
    parts = _visible(page, OUTPUT).all_inner_texts()
    if not parts:
        parts = _visible(page, OUTPUT_FALLBACK).all_inner_texts()
    return " ".join(part.strip() for part in parts if part.strip())


@contextmanager
def _observe_output(page, callback):
    """Forward target DOM changes while connected; never use the clipboard."""
    name = '__rtcTranslation_' + uuid.uuid4().hex
    state = {'active': True, 'pending': None}
    def changed(_, value):
        if state['active'] and isinstance(value, str):
            # Playwright runs bindings inside its dispatcher. Never call sync
            # Playwright APIs (including closing tabs) or SQLite from here.
            state['pending'] = value
    def flush():
        value = state['pending']
        state['pending'] = None
        if value is not None and state['active']:
            callback(value)
    state['flush'] = flush
    page.expose_binding(name, changed)
    try:
        page.evaluate('''({name, primary, fallback}) => {
            const visible = selector => Array.from(document.querySelectorAll(selector))
                .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
            const read = () => {
                let nodes = visible(primary);
                if (!nodes.length) nodes = visible(fallback);
                return nodes.map(el => el.innerText.trim()).filter(Boolean).join(' ');
            };
            let last = read();
            const observer = new MutationObserver(() => {
                const text = read();
                if (text === last) return;
                last = text;
                window[name](text).catch(() => {});
            });
            observer.observe(document.body, {subtree: true, childList: true,
                characterData: true, attributes: true,
                attributeFilter: ['class', 'style', 'hidden', 'aria-hidden']});
            window[name + '_observer'] = observer;
        }''', {'name': name, 'primary': OUTPUT, 'fallback': OUTPUT_FALLBACK})
        yield state
    finally:
        state['active'] = False
        try:
            page.evaluate('''name => {
                window[name + '_observer']?.disconnect();
                delete window[name + '_observer'];
                delete window[name];
            }''', name)
        except Exception:
            pass  # Closing or navigating the page destroys its observer too.


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


def _translate_page(page, text, source, target, cancel, timeout_s=30, append=False, latest=None, on_result=None):
    _check_cancel(cancel)
    if not _is_translate_url(page.url):
        raise TranslationError("The Google Translate tab navigated away. Open Translate again and Retry.")
    params = _translation_params(source, target)
    source, target = params['sl'], params['tl']
    parsed = urlsplit(page.url)
    current = parse_qs(parsed.query)
    clean_url = f"https://{parsed.hostname}/?{urlencode(params)}"
    if append:
        # A stream never navigates/reloads or clears its source, including Retry.
        if any(current.get(k, [''])[0] != params[k] for k in ('sl', 'tl')):
            raise TranslationError('Ngôn ngữ trong tab Google Translate đã đổi. Chọn lại ngôn ngữ trong app để tiếp tục; nội dung tab được giữ nguyên.')
        box = _visible(page, SOURCE).first
        box.wait_for(state='visible', timeout=10_000)
        return _append_stream(page, box, text, cancel, timeout_s, latest, on_result)
    if any(current.get(key, [""])[0] != value for key, value in params.items()):
        page.goto(clean_url, wait_until="load" if append else "domcontentloaded", timeout=10_000)
        if append:
            _pause(cancel, 1.0)
        page.evaluate("name => window.name = name", TAB_NAME)
    input_box = _visible(page, SOURCE).first
    input_box.wait_for(state="visible", timeout=10_000)
    _check_cancel(cancel)
    return _replace_and_translate(page, input_box, text, source, target, cancel, clean_url, timeout_s)


def _append_source(box, text):
    current = box.input_value(timeout=1500)
    # Google accepts 5,000 characters per text translation even though the
    # textarea reports maxLength=-1. Check before dispatching any input event.
    if len(text.encode('utf-16-le')) // 2 > 5000:
        raise TranslationError('Google Translate chỉ nhận 5.000 ký tự mỗi lượt. Nguồn vượt giới hạn; app giữ nguyên tab, không xóa hoặc ghi đè nội dung.')
    if not text.startswith(current):
        raise TranslationError('Ô nguồn Google Translate đã thay đổi. App giữ nguyên nội dung, không xóa hoặc ghi đè. Hãy khôi phục nội dung nguồn rồi Đồng bộ lại.')
    if text == current:
        return False
    status = box.evaluate('''(el, args) => {
        if (el.value !== args.before) return 'changed';
        if (el.maxLength > 0 && args.text.length > el.maxLength) return 'limit';
        el.setRangeText(args.text.slice(args.before.length), el.value.length, el.value.length, 'end');
        el.dispatchEvent(new Event('input', {bubbles: true}));
        return 'ok';
    }''', {'before': current, 'text': text})
    if status == 'limit':
        raise TranslationError('Ô nguồn Google Translate đã đạt giới hạn ký tự. App giữ nguyên nội dung, không tự xóa hoặc chia lại đoạn.')
    if status != 'ok':
        raise TranslationError('Ô nguồn Google Translate đang được chỉnh sửa. App không ghi đè; hãy Đồng bộ lại.')
    return True


def _append_stream(page, box, text, cancel, timeout_s, latest, on_result):
    published = {'text': None}
    def publish(value):
        _check_cancel(cancel)
        if on_result and value != published['text']:
            on_result(value)
            published['text'] = value
    with _observe_output(page, publish) as observer:
        return _follow_output(page, box, text, cancel, timeout_s, latest, publish, observer)


def _follow_output(page, box, text, cancel, timeout_s, latest, on_result, observer):
    language_query = parse_qs(urlsplit(page.url).query)
    languages = (language_query.get('sl'), language_query.get('tl'))
    previous = _read_output(page)
    before = box.input_value(timeout=1500)
    wrote = _append_source(box, text)
    last, changed = '', time.monotonic()
    source_changed, published = changed, ''
    same_words = lambda a, b: re.sub(r'[\s.,!?;:。！？、，：；…]+', '', a).casefold() == re.sub(r'[\s.,!?;:。！？、，：；…]+', '', b).casefold()
    fresh = not wrote or not previous or same_words(before, text)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _check_cancel(cancel)
        observer['flush']()  # Back in the owning worker, outside the binding.
        query = parse_qs(urlsplit(page.url).query)
        if not _is_translate_url(page.url) or (query.get('sl'), query.get('tl')) != languages:
            raise TranslationError('Tab Google Translate đã đổi trang hoặc ngôn ngữ. App giữ nguyên nội dung; hãy kiểm tra tab rồi Đồng bộ lại.')
        # Continue appending incoming transcript while Google is still translating.
        incoming = latest() if latest else None
        if incoming and incoming != text:
            previous = _read_output(page)
            _append_source(box, incoming)
            fresh = not previous or same_words(text, incoming)
            text = incoming
            source_changed = time.monotonic()
            deadline = time.monotonic() + timeout_s
        if box.input_value(timeout=1500) != text:
            raise TranslationError('Ô nguồn Google Translate đã thay đổi hoặc không nhận đủ chữ. App giữ nguyên nội dung; hãy Đồng bộ lại.')
        result = _read_output(page)
        if result != previous:
            fresh = True
        if result != last:
            last, changed = result, time.monotonic()
        if fresh and result:
            if on_result and result != published:
                _check_cancel(cancel)
                on_result(result)
                published = result
            if time.monotonic() - changed >= .8 and time.monotonic() - source_changed >= 1.2:
                # Read the right-hand text directly; copying is unnecessary for sync.
                return result
        _pause(cancel)
    raise TranslationError('Google Translate chưa cập nhật bản dịch. Nội dung nguồn vẫn được giữ nguyên; bấm Đồng bộ để đọc lại.')


def _replace_and_translate(page, input_box, text, source, target, cancel, clean_url, timeout_s):
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
    return _wait_result(page, input_box, text, source, target, cancel, timeout_s)


def _wait_result(page, input_box, text, source, target, cancel, timeout_s, previous_output=''):
    deadline = time.monotonic() + timeout_s
    last, changed = "", time.monotonic()
    fresh = not previous_output
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
        if result != previous_output:
            fresh = True
        if result != last:
            last, changed = result, time.monotonic()
        button = _visible(page, COPY).first
        if fresh and result and time.monotonic() - changed >= 0.6 and button.count() and button.is_enabled():
            copied = _copy_result(page, result, cancel)
            _check_cancel(cancel)
            if input_box.input_value(timeout=1500) != text:
                raise TranslationError("Source text changed before the translation could be read. Retry.")
            return copied
        _pause(cancel)
    raise TranslationError(f"Google Translate did not finish within {timeout_s:g} seconds. Check connection, consent/CAPTCHA or changes to the page, then Retry manually.")


def _stream_parts(text):
    """Stable UTF-16 boundaries: growing text never shortens an existing tab."""
    parts, start, size = [], 0, 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xffff else 1
        if size + width > 5000:
            parts.append(text[start:index])
            start, size = index, 0
        size += width
    if start < len(text):
        parts.append(text[start:])
    return parts


def _close_previous_tabs(browser, keep):
    """Retire only app-owned Translate tabs after their output has been saved."""
    if keep.is_closed() or not _is_translate_url(keep.url):
        return
    for context in browser.contexts:
        for page in list(context.pages):
            if page == keep or not _is_translate_url(page.url):
                continue
            try:
                name = page.evaluate('() => window.name')
                if name == TAB_NAME or name.startswith(TAB_NAME + ':'):
                    page.close()
            except Exception:
                pass  # A tab may already be closing; cleanup must not fail translation.


def _translate_stream(browser, text, source, target, cancel, stream_key, latest, on_result, cache):
    current = {'text': text, 'parts': _stream_parts(text)}
    outputs, index = [], 0
    current_page = None
    retired = False
    def retire_previous():
        nonlocal retired
        _check_cancel(cancel)
        if not retired and current_page is not None:
            _close_previous_tabs(browser, current_page)
            retired = True
    while index < len(current['parts']):
        _check_cancel(cancel)
        parts = current['parts']
        part_key = stream_key if index == 0 else stream_key + ':part:' + str(index)
        key = (part_key, source, target, parts[index])
        value = cache.get(key) if cache is not None and index < len(parts) - 1 else None
        if value is None:
            page = _choose_tab(browser, source, target, cancel, stream_key=part_key, text=parts[index])
            current_page = page
            retired = False
            def next_source():
                updated = latest() if latest else None
                if updated and updated != current['text']:
                    if not updated.startswith(current['text']):
                        raise TranslationError('Transcript đã thay đổi phần cũ. App giữ nguyên các tab Google Translate.')
                    current['text'] = updated
                    current['parts'] = _stream_parts(updated)
                return current['parts'][index]
            def publish(raw):
                # Earlier tabs are complete; only publish when the entire visible
                # translation includes the current tail, including after restart.
                if on_result and index == len(current['parts']) - 1:
                    on_result(' '.join(outputs + [raw]))
                    retire_previous()
            value = _translate_page(page, parts[index], source, target, cancel,
                                    append=True, latest=next_source, on_result=publish)
        outputs.append(value)
        parts = current['parts']
        if cache is not None and index < len(parts) - 1:
            cache[(part_key, source, target, parts[index])] = value
            # Closed parts must remain available throughout this session, even
            # for long transcripts. Evict other sessions first, never this one.
            while len(cache) > 64:
                obsolete = next((k for k in cache if k[0] != stream_key
                                 and not k[0].startswith(stream_key + ':part:')), None)
                if obsolete is None:
                    break
                del cache[obsolete]
        index += 1
    result = ' '.join(outputs)
    _check_cancel(cancel)
    if on_result:
        on_result(result)  # Persist the complete snapshot before closing any old tab.
    retire_previous()
    return result


def translate(text, source, target, cancel=None, *, append=False, stream_key='', latest=None, on_result=None, cache=None):
    """Serialize all chunks; keep Chrome and the current Translate tab open."""
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
                raise TranslationError("Could not connect to Chrome on port 9222. Reopen the app or start launch_chrome.bat and Retry.") from None
            if append:
                return _translate_stream(browser, text, source, target, cancel, stream_key, latest, on_result, cache)
            else:
                # Utility tests must not overwrite a live conversation's tab.
                page = _choose_tab(browser, source, target, cancel)
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
