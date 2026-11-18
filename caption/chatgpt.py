"""Ask ChatGPT about a transcript file through the user's logged-in Chrome.

Chrome must be running with ``--remote-debugging-port=9222`` (see
``C:\\Users\\ADMIN\\ai-orchestrator\\launch_chrome.bat``). We attach over CDP with
Playwright, attach files, type the selected prompt, send, wait for the answer and
return it as markdown. Chat and Summary can use the user's current ChatGPT tab;
New words keeps a dedicated tab marked via ``window.name``.

While ChatGPT answers, preview the new turn's rendered assistant text. Once the
response action bar appears and generation stops, read the final markdown using
its Copy button while intercepting the page's clipboard calls.

Selectors are the part most likely to break when ChatGPT changes its UI.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .chatgpt_actions import queued_action
from .chatgpt_source import CONVERSATION_MAPPING, response_position

log = logging.getLogger(__name__)

CDP_URL = "http://localhost:9222"
CHATGPT_URL = "https://chatgpt.com/"
# Legacy/default dedicated tab name. Chat and Summary now request the current ordinary
# ChatGPT tab; New words still uses its own named tab.
APP_TAB = "real-time-caption-summary"
VOCAB_TAB = "real-time-caption-vocab"
CHAT_TAB_PREFIX = "real-time-caption-chat:"
DICTATION_TAB_PREFIX = "real-time-caption-dictation:"


def is_dictation_tab(key):
    return isinstance(key, str) and key.startswith(DICTATION_TAB_PREFIX)


_TAB_SCAN_LOCK = threading.Lock()
# Summary and Chat prompts are versioned in their own folders (see prompt_versions.py).

# Keep this scoped to ChatGPT's real composer.  A conversation can contain other
# visible contenteditable widgets (for example the CodeMirror "Edit code" pane).
# The old broad ``div[contenteditable][aria-label]`` selector could pick one of
# those before the message box and then fail while looking for its composer form.
COMPOSER = (
    "form[data-chatgpt-composer] [data-composer-markdown][contenteditable='true'], "
    "form[data-thread-find-composer] [contenteditable='true'][role='textbox'], "
    "form #prompt-textarea, "
    "form div.ProseMirror[contenteditable='true'][role='textbox']"
)
# ChatGPT has several hidden file inputs; the image-only ones reject .txt files.
FILE_INPUT = "input[type='file']:not([accept*='image'])"
SEND_BUTTON = (
    "#composer-submit-button, "
    "button[data-testid='send-button'], "
    "button[type='submit'][aria-label='Send' i], "
    "button[type='submit'][aria-label='Gửi' i]"
)
STOP_BUTTON = (
    "button[data-testid='stop-button'], "
    "button[aria-label='Stop streaming' i], "
    "button[aria-label='Stop generating' i]"
)
# One element per question/answer pair; the attribute value identifies the turn.
TURN = "[data-turn-key], section[data-testid^='conversation-turn-']"
TURN_ID_ATTRS = ["data-turn-key", "data-testid"]
# Only the Copy control in the response action bar. Code blocks also expose a
# button whose aria-label is exactly "Copy", so an unscoped selector can copy
# one code block instead of the complete response (and can signal completion
# before ChatGPT has finished generating the answer).
REPLY_COPY_BUTTON = (
    "button[data-testid='copy-turn-action-button'], "
    ".turn-action-controls button[aria-label='Copy'], "
    ".turn-action-controls button[aria-label='Copy response']"
)
# "Power" (reasoning effort) lives in the model picker as a keyboard-driven slider.
MODEL_PICKER = "button[aria-label='Select ChatGPT model'], button[data-testid='model-switcher-dropdown-button']"
POWER_ITEM = "[role=menu] [role=menuitem][data-reasoning-slider='true']"
POWER_SLIDER = "[role=menu] [role=slider]"
POWER_STATUS = "[role=menu] [role=status]"
# Names ChatGPT shows for the slider steps (reasoning effort none / medium / high).
POWER_LEVELS = ["Instant", "Medium", "High"]
UPLOAD_TIMEOUT_S = 120
ANSWER_TIMEOUT_S = 600
SENT_TIMEOUT_S = 20
QUIET_S = 2.5  # answer text must stop changing this long before we read it
STREAM_POLL_S = 0.35

# Current builds group user/assistant content in one turn. Read only assistant
# message containers, never the whole turn (which also contains the question).
_RESPONSE_TEXT_JS = """
  const responseText = (turn) => {
    let nodes = [...turn.querySelectorAll(
      '[data-content-search-unit-key$=":assistant"], ' +
      '[data-chatgpt-search-unit-key$=":assistant"], ' +
      '[data-message-author-role="assistant"]'
    )];
    nodes = nodes.filter(n => !nodes.some(parent => parent !== n && parent.contains(n)));
    if (!nodes.length) {
      nodes = [...turn.querySelectorAll('.markdown, .prose')].filter(n =>
        !n.closest('[data-message-author-role="user"], [data-user-message-bubble], [class~="group/user-message"]')
      );
      nodes = nodes.filter(n => !nodes.some(parent => parent !== n && parent.contains(n)));
    }
    return nodes.map(n => n.innerText || '').join('\\n\\n').trim();
  };
"""

# Intercept the page's clipboard writes so the Copy button hands us the markdown
# without touching the user's real clipboard (same trick as ai-orchestrator).
COPY_HOOK = """() => {
  if (window.__rtcClipboard) return null; // another copy operation owns these hooks
  const c = navigator.clipboard;
  const state = {items: [], wt: c?.writeText, w: c?.write, exec: document.execCommand};
  window.__rtcClipboard = state;
  state.writeText = async t => { state.items.push({k: 'text/plain', t: String(t)}); };
  state.write = async items => {
    for (const it of items) for (const k of it.types) {
      try { state.items.push({k, t: await (await it.getType(k)).text()}); } catch (e) {}
    }
  };
  state.execCommand = function(command, ...args) {
    if (String(command).toLowerCase() !== 'copy') return state.exec.call(this, command, ...args);
    // ChatGPT's window capture listener stops propagation before document listeners.
    // Dispatch locally, then read the data it supplied after all handlers finished.
    // A synthetic copy event never writes to the system clipboard.
    const data = new DataTransfer();
    const target = document.activeElement || document;
    target.dispatchEvent(new ClipboardEvent('copy', {
      clipboardData: data, bubbles: true, cancelable: true, composed: true
    }));
    for (const k of data.types) state.items.push({k, t: data.getData(k)});
    return data.types.length > 0;
  };
  document.execCommand = state.execCommand;
  if (c) { c.writeText = state.writeText; c.write = state.write; }
  return document.execCommand === state.execCommand &&
    (!c || (c.writeText === state.writeText && c.write === state.write));
}"""

COPY_READ = "() => window.__rtcClipboard?.items || []"

COPY_UNHOOK = """() => {
  const state = window.__rtcClipboard;
  if (!state) return [];
  const c = navigator.clipboard;
  if (c?.writeText === state.writeText) c.writeText = state.wt;
  if (c?.write === state.write) c.write = state.w;
  if (document.execCommand === state.execCommand) document.execCommand = state.exec;
  delete window.__rtcClipboard;
  return state.items;
}"""

# Shared JS helper: id of the turn that contains element ``e``.
_TURN_ID_JS = """
  const turnId = (e, a) => {
    const t = e.closest(a.turn);
    if (!t) return null;
    for (const attr of a.attrs) { const v = t.getAttribute(attr); if (v) return v; }
    return null;
  };
"""


class ChatGPTError(RuntimeError):
    pass


class ChatGPTTabClosed(ChatGPTError):
    """The explicitly selected tab cannot be found before sending a request."""


class ChatGPTCopyError(ChatGPTError):
    def __init__(self, message, source=None, title=''):
        super().__init__(message)
        self.source = source or {}
        self.title = title


CANCELLED_MESSAGE = "Cancelled by user. You can press Retry to send the same request again."


class ChatGPTCancelled(ChatGPTError):
    pass


class ChatGPTContextFull(ChatGPTError):
    """ChatGPT explicitly blocked this conversation at its context limit."""


class ChatGPTVerificationRequired(ChatGPTError):
    code = 'chatgpt_verification_required'

    def __init__(self):
        super().__init__(
            'ChatGPT đang yêu cầu xác minh bạn là người (Verify you are human). '
            'Tác vụ trên tab này đã tạm dừng. Hãy mở đúng tab ChatGPT và hoàn tất xác minh thủ công. '
            'App không tự đóng tab, đổi tab hoặc gửi lại câu hỏi.'
        )


_OUTSIDE_CONVERSATION = (
    'xpath=self::*[not(ancestor-or-self::*['
    '@data-message-author-role or @data-turn-key or '
    'starts-with(@data-testid, "conversation-turn-") or '
    '@contenteditable="true" or @data-chatgpt-composer or @data-thread-find-composer])]'
)


def _check_verification_required(page):
    """Read visible product notices only; never interact with a verification widget."""
    notice = page.get_by_text(re.compile(
        r'^\s*(?:Verify (?:that )?you are human|Confirm (?:that )?you are human|'
        r'Verifying you are human(?:\. This may take a few seconds)?|'
        r'Xác minh (?:rằng )?bạn là (?:con )?người|Xác nhận bạn là người)[.!…\s]*$', re.I
    )).filter(visible=True).locator(_OUTSIDE_CONVERSATION).first
    widget = page.locator(
        '#challenge-form, #challenge-running, '
        'iframe[src*="challenges.cloudflare.com"], '
        'iframe[title*="recaptcha challenge" i]'
    ).filter(visible=True).locator(_OUTSIDE_CONVERSATION).first
    if notice.is_visible() is True or widget.is_visible() is True:
        raise ChatGPTVerificationRequired()


def _check_context_full(page):
    _check_verification_required(page)
    # Match the product notice, not the same words quoted in a reply or draft.
    notice = page.get_by_text(re.compile(
        r'^\s*(?:Context window full|Cửa sổ ngữ cảnh đã đầy)\s*$', re.I
    )).filter(visible=True).locator(
        'xpath=self::*[not(ancestor-or-self::*['
        '@data-message-author-role or @data-turn-key or '
        'starts-with(@data-testid, "conversation-turn-") or '
        '@contenteditable="true" or @data-chatgpt-composer or @data-thread-find-composer])]').first
    if notice.is_visible() is True:
        raise ChatGPTContextFull(
            'Cuộc hội thoại ChatGPT đã đầy ngữ cảnh (Context window full). '
            'Mở tab ChatGPT này để chuyển sang cuộc trò chuyện mới kèm tóm tắt. '
            'Cần xử lý giới hạn ngữ cảnh trước khi tiếp tục trong app. '
            'App đã dừng lượt xử lý, không tự gửi lại câu hỏi.'
        )


@dataclass(frozen=True)
class ChromeChatGPTTab:
    key: str
    label: str
    title: str
    url: str
    focused: bool


def build_prompt(
    file_path: Path | None,
    source: str,
    started: datetime | None,
    template_path: Path,
    message: str = "",
) -> str:
    """Fill ``{file_name}``, ``{source}``, ``{started}``, ``{now}`` and ``{message}`` in an
    editable prompt file."""
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChatGPTError(f"Could not read the prompt file {template_path}:\n{exc}") from exc
    values = {
        "file_name": file_path.name if file_path else "",
        "source": source,
        "started": f"{started:%Y-%m-%d %H:%M:%S}" if started else "",
        "now": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "message": message.strip(),
    }
    # Plain replacement rather than str.format, so other braces in the prompt are left alone.
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template.strip()


_CITATION = re.compile(
    r"[ \t]*(?::chatgpt-content-reference\{[^}]*\}|:contentReference\[[^\]]*\]\{[^}]*\}|【[^】]*】)"
)


def clean_answer(text: str) -> str:
    """Drop ChatGPT's file-citation markers, which are meaningless outside chatgpt.com."""
    return _CITATION.sub("", text).strip()


# --------------------------------------------------------------------- tab helpers


def _dedicated_tab(browser, tab_name: str, cancel_event=None, on_status=None):
    """Reuse our dedicated tab if it is still open, otherwise open a new one.

    Returns ``(page, created)``.
    """
    if is_dictation_tab(tab_name):
        raise ChatGPTError('Tab thu âm chỉ dùng cho transcript, không nhận câu hỏi.')
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" not in page.url:
                continue
            try:
                if page.evaluate("window.name") == tab_name:
                    return page, False
            except Exception:  # noqa: BLE001 - tab closing or crashed
                continue
    if not browser.contexts:
        raise ChatGPTError("Chrome has no open window to add a tab to.")
    return _open_background_tab(browser, cancel_event=cancel_event, on_status=on_status), True


def _current_chatgpt_tab(browser, cancel_event=None, on_status=None):
    """Use the user's current ordinary ChatGPT tab, without taking over Voca's tab.

    ``document.hasFocus()`` identifies the active tab in the focused Chrome window;
    visibility is the fallback for another Chrome window. If no ordinary ChatGPT tab
    exists, create one rather than mixing Chat/Summary into Voca's dedicated thread.
    """
    candidates = []
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" not in page.url:
                continue
            try:
                key = page.evaluate("window.name")
                if key == VOCAB_TAB or is_dictation_tab(key):
                    continue
                focused = bool(page.evaluate("document.hasFocus()"))
                visible = page.evaluate("document.visibilityState") == "visible"
                candidates.append((focused, visible, len(candidates), page))
            except Exception:  # noqa: BLE001 - tab closing or crashed
                continue
    if candidates:
        return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3], False
    if not browser.contexts:
        raise ChatGPTError("Chrome has no open window to add a tab to.")
    return _open_background_tab(browser, cancel_event=cancel_event, on_status=on_status), True


def _selected_chatgpt_tab(browser, tab_key: str):
    """Return the ordinary ChatGPT tab carrying ``tab_key`` or fail without opening another."""
    if is_dictation_tab(tab_key):
        raise ChatGPTError('Tab thu âm chỉ dùng cho transcript, không nhận câu hỏi.')
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" not in page.url:
                continue
            try:
                if page.evaluate("window.name") == tab_key:
                    return page, False
            except Exception:  # noqa: BLE001 - tab closing or crashed
                continue
    raise ChatGPTTabClosed("The selected ChatGPT tab is no longer open. Press Refresh and choose another tab.")


@queued_action
def list_chrome_chatgpt_tabs(cancel_event=None) -> list[ChromeChatGPTTab]:
    """Scan Chrome's ordinary ChatGPT tabs and give each a stable, invisible key."""
    # Chat and Summary scan on separate worker threads during startup. Serializing
    # them prevents both scans assigning different keys to the same unnamed tab.
    with _TAB_SCAN_LOCK:
        return _list_chrome_chatgpt_tabs()


def _list_chrome_chatgpt_tabs() -> list[ChromeChatGPTTab]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError(
                f"Could not connect to Chrome at {CDP_URL}. Start Chrome with remote debugging and try again."
            ) from exc
        found: list[tuple[str, str, str, bool, bool]] = []
        for context in browser.contexts:
            for page in context.pages:
                if "chatgpt.com" not in page.url:
                    continue
                try:
                    key = page.evaluate("window.name")
                    if key == VOCAB_TAB or is_dictation_tab(key):
                        continue
                    if not isinstance(key, str) or not key.startswith(CHAT_TAB_PREFIX):
                        key = CHAT_TAB_PREFIX + uuid.uuid4().hex
                        page.evaluate("key => window.name = key", key)
                    title = page.title().strip() or "ChatGPT"
                    focused = bool(page.evaluate("document.hasFocus()"))
                    visible = page.evaluate("document.visibilityState") == "visible"
                    found.append((key, title, page.url, focused, visible))
                except Exception:  # noqa: BLE001 - tab closing during the scan
                    continue
        # Current/focused tabs appear first. The short stable key makes duplicate Chrome
        # titles unambiguous while leaving the real displayed title intact at the front.
        found.sort(key=lambda item: (item[3], item[4]), reverse=True)
        return [
            ChromeChatGPTTab(
                key=key,
                label=f"{title} — {key.removeprefix(CHAT_TAB_PREFIX)[:8]}",
                title=title,
                url=url,
                focused=focused,
            )
            for key, title, url, focused, _visible_state in found
        ]


def _open_background_tab(browser, *, chatgpt_tab=True, cancel_event=None, on_status=None):
    if not chatgpt_tab:
        return _create_background_tab(browser)
    from .chatgpt_tab_gate import opening
    with opening(browser, cancel_event, on_status) as ticket:
        page = _create_background_tab(browser)
        ticket.opened(page)
        return page


def _create_background_tab(browser):
    """Open a tab without switching Chrome to it (Playwright's new_page() activates the tab)."""
    context = browser.contexts[0]
    creation_requested = False
    session = None
    try:
        session = browser.new_browser_cdp_session()
        with context.expect_page(timeout=10_000) as new_page:
            creation_requested = True
            session.send("Target.createTarget", {"url": "about:blank", "background": True})
        return new_page.value
    except Exception:  # noqa: BLE001
        if creation_requested:
            raise ChatGPTError('Đã gửi yêu cầu mở tab nhưng chưa xác nhận được tab mới. Hãy kiểm tra Chrome.') from None
        log.warning("Could not open a background tab; falling back to a normal one", exc_info=True)
        return context.new_page()
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                log.debug('Browser session already detached', exc_info=True)


@contextmanager
def _return_to_web_tab(browser, tab_id):
    """Restore only the app document that requested the action, even on failure."""
    matches = []
    if tab_id and len(tab_id) <= 100:
        for context in browser.contexts:
            for page in context.pages:
                if urlsplit(page.url).hostname not in ('localhost', '127.0.0.1', '::1'):
                    continue
                try:
                    if page.evaluate('(id) => document.documentElement?.dataset.rtcTabId === id', tab_id):
                        matches.append(page)
                except Exception:
                    pass  # A page can close or navigate during discovery.
    caller = matches[0] if len(matches) == 1 else None
    state = {'restored': False}
    try:
        yield state
    finally:
        if caller:
            try:
                if not caller.is_closed():
                    caller.bring_to_front()
                    state['restored'] = True
            except Exception:
                # Focus restoration must not turn a successfully created tab into
                # a failed request (which could cause duplicate branches on retry).
                log.warning('Could not return to the calling app tab', exc_info=True)


def _open_new_chatgpt_tab(browser, return_tab='', cancel_event=None, on_status=None):
    with _return_to_web_tab(browser, return_tab) as focus:
        tab = _open_new_chatgpt_tab_impl(browser, cancel_event, on_status)
    return replace(tab, focused=False) if focus['restored'] else tab


def _open_new_chatgpt_tab_impl(browser, cancel_event=None, on_status=None):
    if not browser.contexts:
        raise ChatGPTError('Chrome chưa có cửa sổ để mở tab ChatGPT.')
    page = _open_background_tab(browser, cancel_event=cancel_event, on_status=on_status)
    try:
        page.goto(CHATGPT_URL, wait_until='domcontentloaded', timeout=30_000)
        with _TAB_SCAN_LOCK:
            key = page.evaluate('([prefix, proposed]) => { if (!window.name.startsWith(prefix)) window.name = proposed; return window.name; }',
                                [CHAT_TAB_PREFIX, CHAT_TAB_PREFIX + uuid.uuid4().hex])
        page.bring_to_front()
        title = page.title().strip() or 'ChatGPT'
        return ChromeChatGPTTab(key=key, label=f'{title} — {key.removeprefix(CHAT_TAB_PREFIX)[:8]}',
                               title=title, url=page.url, focused=True)
    except Exception as exc:
        raise ChatGPTError('Tab mới đã mở nhưng chưa tải xong ChatGPT. Kiểm tra tab đó rồi Refresh danh sách tab.') from exc


def _js_args() -> dict:
    return {"turn": TURN, "attrs": TURN_ID_ATTRS, "copy": REPLY_COPY_BUTTON}


def _visible(page, selector: str):
    """Return the first currently rendered match, ignoring hidden duplicate UI trees."""
    return page.locator(selector).filter(visible=True).first


def _turn_ids(page) -> list[str]:
    return page.evaluate(
        "(a) => {" + _TURN_ID_JS + """
           return [...document.querySelectorAll(a.turn)]
             .map((t) => { for (const x of a.attrs) { const v = t.getAttribute(x); if (v) return v; } return null; })
             .filter(Boolean);
         }""",
        _js_args(),
    )


def _answered_turn_ids(page) -> list[str]:
    """Turns whose answer is complete (their Copy button exists)."""
    return page.evaluate(
        "(a) => {" + _TURN_ID_JS + """
           return [...document.querySelectorAll(a.copy)].map((b) => turnId(b, a)).filter(Boolean);
         }""",
        _js_args(),
    )


def _turn_locator(page, turn_id: str):
    return page.locator(
        ", ".join(f"[{attr}={_css_string(turn_id)}]" for attr in TURN_ID_ATTRS)
    ).first


def _css_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check_cancel(cancel_event):
    if cancel_event and cancel_event.is_set():
        raise ChatGPTCancelled(CANCELLED_MESSAGE)


_COMPOSER_TEXT = """el => {
    if (typeof el.value === 'string') return el.value;
    // innerText inserts layout-dependent blank lines between <p> elements.
    // Read logical editor lines instead, preserving empty paragraphs and code
    // indentation. textContent alone would concatenate adjacent paragraphs.
    const blocks = new Set(['P', 'DIV', 'PRE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE']);
    const read = node => {
        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || '';
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        if (node.tagName === 'BR') return node.classList.contains('ProseMirror-trailingBreak') ? '' : '\\n';
        const children = [...node.childNodes];
        // A sole BR is the placeholder of an empty paragraph, not two lines.
        if (blocks.has(node.tagName) && children.length === 1 && children[0].nodeName === 'BR') return '';
        const hasBlocks = children.some(child => blocks.has(child.nodeName));
        const parts = [];
        let inline = '';
        for (const child of children) {
            if (blocks.has(child.nodeName)) {
                if (inline) { parts.push(inline); inline = ''; }
                parts.push(read(child));
            } else {
                if (hasBlocks && child.nodeType === Node.TEXT_NODE && !child.nodeValue.trim()) continue;
                inline += read(child);
            }
        }
        if (inline) parts.push(inline);
        return parts.join('\\n');
    };
    return read(el);
}"""
_INSERT_COMPOSER_TEXT = """(el, text) => {
    if (!el.isConnected || !el.getClientRects().length ||
        getComputedStyle(el).visibility === 'hidden' || el.disabled || el.readOnly ||
        el.getAttribute('aria-disabled') === 'true') return false;
    const input = el.tagName === 'TEXTAREA' ||
        (el.tagName === 'INPUT' && ['text', 'search'].includes(el.type));
    if (!input && !el.isContentEditable) return false;
    // Focus, selection and insertion run atomically in this document. Never use
    // global keyboard/clipboard or assign innerHTML (which bypasses editor state).
    el.focus({preventScroll: true});
    if (document.activeElement !== el) return false;
    if (input) {
        el.select();
        el.setRangeText(text, 0, el.value.length, 'end');
        el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: text}));
        return true;
    }
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return document.execCommand('insertText', false, text);
}"""


def _fill_composer(page, composer, prompt, cancel_event=None):
    """Bounded pre-send retries; background editors may stall Playwright's fill."""
    def matches():
        value = composer.evaluate(_COMPOSER_TEXT, timeout=1500)
        normalize = lambda text: text.replace('\r\n', '\n').replace('\xa0', ' ').strip('\n')
        return isinstance(value, str) and normalize(value) == normalize(prompt)

    for attempt in range(2):
        _check_cancel(cancel_event)
        _check_context_full(page)
        try:
            composer.fill(prompt, timeout=3000)
        except Exception as exc:
            # Do not log Playwright's call log: it contains the entire prompt.
            log.info('ChatGPT composer fill attempt %s failed (%s)', attempt + 1, type(exc).__name__)
        _check_cancel(cancel_event)
        _check_context_full(page)
        try:
            # A fill can time out after the browser has already applied it.
            if not matches():
                if composer.evaluate(_INSERT_COMPOSER_TEXT, prompt, timeout=1500) is not True:
                    continue
            page.wait_for_timeout(150)  # Let the editor process its input/mutation handlers.
            _check_cancel(cancel_event)
            if matches():
                return
        except ChatGPTCancelled:
            raise
        except Exception as exc:
            log.info('ChatGPT composer verification failed (%s)', type(exc).__name__)
    _check_cancel(cancel_event)
    _check_context_full(page)
    raise ChatGPTError(
        'Chưa xác nhận được đầy đủ nội dung câu hỏi trong ô ChatGPT sau khi thử lại. '
        'Ô nhập có thể đã có chữ nhưng nội dung chưa khớp. App chưa bấm gửi. '
        'Hãy kiểm tra tab ChatGPT rồi thử lại.'
    ) from None


def _wait_not_answering(page, on_status: Callable[[str], None], cancel_event=None) -> None:
    """When continuing a conversation, let ChatGPT finish its previous answer first."""
    stop = _visible(page, STOP_BUTTON)
    try:
        if stop.count() == 0 or not stop.is_visible():
            return
        on_status("Waiting for ChatGPT to finish its previous answer…")
        deadline = time.monotonic() + ANSWER_TIMEOUT_S
        while stop.count() and stop.is_visible():
            # The previous generation is not this request's answer: never stop it here.
            _check_cancel(cancel_event)
            _check_context_full(page)
            if time.monotonic() >= deadline:
                raise ChatGPTError('Previous answer is still running.')
            time.sleep(.25)
    except (ChatGPTCancelled, ChatGPTVerificationRequired, ChatGPTContextFull):
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChatGPTError("ChatGPT is still answering the previous message. Try again when it finishes.") from exc


def _set_power(page, level: int) -> str:
    """Set and verify committed effort, using keys scoped to this tab's control."""
    if level not in range(len(POWER_LEVELS)):
        raise ChatGPTError('Invalid ChatGPT Power level.')
    picker = _visible(page, MODEL_PICKER)
    if picker.count() == 0:
        raise ChatGPTError("Could not find ChatGPT's model picker to set the power level.")
    picker.click(timeout=5000)
    try:
        power = _visible(page, POWER_ITEM)
        try:
            power.wait_for(state="visible", timeout=5000)
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError("ChatGPT's model picker has no Power slider (the layout may have changed).") from exc
        slider = _visible(page, POWER_SLIDER)
        if level > int(slider.get_attribute("aria-valuemax") or len(POWER_LEVELS) - 1):
            raise ChatGPTError(f'ChatGPT không hỗ trợ mức Power {POWER_LEVELS[level]} trong tab này.')
        target = level
        changed = False
        for _ in range(len(POWER_LEVELS) + 2):
            current = int(slider.get_attribute("aria-valuenow") or 0)
            if current == target:
                break
            # Refocus the exact control for every key. Other ChatGPT workers or
            # the user may move focus between steps; page.keyboard alone is unsafe.
            power.press("ArrowRight" if current < target else "ArrowLeft", timeout=5000)
            changed = True
            deadline = time.monotonic() + 2
            while int(slider.get_attribute("aria-valuenow") or 0) == current and time.monotonic() < deadline:
                page.wait_for_timeout(50)
        if int(slider.get_attribute("aria-valuenow") or -1) != target:
            raise ChatGPTError("Could not move ChatGPT's Power slider to the chosen level.")
        if changed:
            # The slider can show a temporary value. Reopen it to verify what
            # ChatGPT actually kept before any question/transcript is sent.
            power.press("Escape", timeout=2000)
            picker.click(timeout=5000)
            power.wait_for(state='visible', timeout=5000)
            if int(slider.get_attribute('aria-valuenow') or -1) != target:
                raise ChatGPTError(f'ChatGPT chưa giữ mức Power {POWER_LEVELS[level]}. Chưa gửi câu hỏi; hãy thử lại.')
        # Status reads e.g. "Medium, 2 of 3."
        return _visible(page, POWER_STATUS).inner_text().split(",")[0].strip()
    except ChatGPTError:
        raise
    except Exception as exc:
        raise ChatGPTError(f'Không xác nhận được Power {POWER_LEVELS[level]} trên ChatGPT. Chưa gửi câu hỏi.') from exc
    finally:
        power = _visible(page, POWER_ITEM)
        if power.count() and power.is_visible():
            try:
                power.press('Escape', timeout=2000)
            except Exception:
                pass


def _composer_form(composer):
    """The visible composer's form; ChatGPT may keep another hidden conversation in the DOM."""
    return composer.locator("xpath=ancestor::form[1]")


def _clear_attachments(form) -> int:
    """Drop attachments left in this composer by an earlier attempt that never got sent.

    The remove buttons only take pointer events on hover, so they are clicked from JS.
    """
    removed = 0
    while removed < 20 and form.evaluate(
        """form => {
             const b = form.querySelector('button[aria-label^="Remove "]');
             if (!b) return false;
             b.click();
             return true;
           }"""
    ):
        removed += 1
        time.sleep(0.3)
    return removed


def _wait_attachments_visible(form, attachments: list[Path], timeout_s: float, cancel_event=None,
                              on_wait=None, page=None) -> None:
    """Do not send until every selected file appears in the visible composer's chips."""
    expected = {path.name for path in attachments}
    deadline = time.monotonic() + timeout_s
    remove_buttons = form.locator("button[aria-label^='Remove ']")
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        if page is not None:
            _check_context_full(page)
        if on_wait:
            on_wait()
        try:
            labels = remove_buttons.evaluate_all(
                "buttons => buttons.map(button => button.getAttribute('aria-label') || '')"
            )
            found = {name for name in expected if any(name in label for label in labels)}
            if found == expected:
                return
        except Exception:  # noqa: BLE001 - attachment UI can re-render while uploading
            pass
        time.sleep(0.25)
    missing = ", ".join(sorted(expected))
    raise ChatGPTError(
        f"ChatGPT did not attach {missing}. The file input may have changed; nothing was sent."
    )


def _wait_send_enabled(scope, timeout_s: float, cancel_event=None, page=None, on_wait=None):
    """The send button stays disabled until the attachment has finished uploading."""
    deadline = time.monotonic() + timeout_s
    button = _visible(scope, SEND_BUTTON)
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        if page is not None:
            _check_context_full(page)
        if on_wait:
            on_wait()
        try:
            if button.is_visible() and button.is_enabled():
                return button
        except Exception:  # noqa: BLE001 - button re-rendered between checks
            pass
        time.sleep(0.5)
    raise ChatGPTError(
        'Đã hết thời gian chờ ChatGPT sẵn sàng gửi; file/ảnh có thể vẫn đang xử lý. '
        'Chưa bấm gửi. Kiểm tra tab ChatGPT rồi thử lại.'
    )


def _upload_progress(on_status, started, timeout_s):
    next_update = started

    def report():
        nonlocal next_update
        now = time.monotonic()
        if now >= next_update:
            next_update = now + 5
            on_status(f'Đang chờ ChatGPT xử lý file/ảnh và sẵn sàng gửi: '
                      f'{int(now - started)}/{int(timeout_s)} giây. Có thể bấm Stop để hủy.')
    return report


# ---------------------------------------------------------------- reading answers


def _cancel_if_requested(page, cancel_event: threading.Event | None) -> None:
    if not cancel_event or not cancel_event.is_set():
        return
    try:
        stop = _visible(page, STOP_BUTTON)
        if stop.count() and stop.is_visible():
            stop.click(timeout=2000)
    except Exception:  # noqa: BLE001 - cancellation must still complete if the button changed
        pass
    raise ChatGPTCancelled(CANCELLED_MESSAGE)


def _wait_new_answer(
    page, known: list[str], timeout_s: float, cancel_event: threading.Event | None = None,
    on_partial: Callable[[str], None] | None = None,
    turns_before: list[str] | None = None,
) -> str:
    """Preview the new answer as it grows; finish only at its response Copy button."""
    deadline = time.monotonic() + timeout_s
    last_text = ""
    target = None
    while time.monotonic() < deadline:
        _cancel_if_requested(page, cancel_event)
        _check_context_full(page)
        snapshot = page.evaluate(
            "(a) => {" + _TURN_ID_JS + _RESPONSE_TEXT_JS + """
              const known = new Set(a.known);
              const before = new Set(a.before || []);
              const turns = [...document.querySelectorAll(a.turn)];
              for (let i = turns.length - 1; i >= 0; i--) {
                const turn = turns[i], id = turnId(turn, a);
                if (!id || (a.target && id !== a.target) || known.has(id) || before.has(id)) continue;
                const streaming = [...document.querySelectorAll(a.stop)].some(b =>
                  b.getClientRects().length && getComputedStyle(b).visibility !== 'hidden'
                );
                return {id, text: responseText(turn), complete: !!turn.querySelector(a.copy) && !streaming};
              }
              return null;
            }""",
            {**_js_args(), "stop": STOP_BUTTON, "known": known, "before": turns_before, "target": target},
        )
        if snapshot:
            text = snapshot["text"]
            if text or snapshot["complete"]:
                target = snapshot["id"]
            if text and text != last_text:
                last_text = text
                if on_partial:
                    on_partial(clean_answer(text))
            if snapshot["complete"]:
                return target
        time.sleep(STREAM_POLL_S)
    raise ChatGPTError(f"ChatGPT did not finish answering within {timeout_s // 60} minutes.")


def _answer_text_length(page, turn_id: str) -> int:
    turn = _turn_locator(page, turn_id)
    try:
        return len(turn.inner_text(timeout=5000))
    except Exception:  # noqa: BLE001
        return -1


def _wait_stable(page, turn_id: str, timeout_s: float, cancel_event: threading.Event | None = None) -> None:
    """The Copy button can flash up mid-answer on some builds; also require the text to settle."""
    deadline = time.monotonic() + timeout_s
    last, since = -2, time.monotonic()
    while time.monotonic() < deadline:
        _cancel_if_requested(page, cancel_event)
        _check_context_full(page)
        n = _answer_text_length(page, turn_id)
        now = time.monotonic()
        if n != last:
            last, since = n, now
        elif now - since >= QUIET_S:
            return
        time.sleep(0.4)


def _response_source(turn, turn_id: str) -> dict:
    """Read the exact copied turn, never infer an absolute index from a virtualized DOM."""
    source = dict(response_conversation_url='', response_turn_id=turn_id, response_message_id='', response_position=None)
    try:
        value = turn.evaluate('''turn => {
            const explicit = [...turn.querySelectorAll('[data-message-author-role="assistant"][data-message-id]')];
            const units = [...turn.querySelectorAll('[data-content-search-unit-key$=":assistant"], [data-chatgpt-search-unit-key$=":assistant"]')];
            const final = explicit.at(-1);
            const unit = units.at(-1);
            const ids = (unit?.getAttribute('data-chatgpt-search-message-ids') || '').trim().split(/\\s+/).filter(Boolean);
            return {url: turn.ownerDocument.location.href,
                message_id: final?.getAttribute('data-message-id') || ids.at(-1) || ''};
        }''')
        url = urlsplit(value['url'])
        if url.scheme == 'https' and url.hostname in ('chatgpt.com', 'www.chatgpt.com', 'chat.openai.com') and re.search(r'/c/[^/]+/?$', url.path):
            source['response_conversation_url'] = value['url']
        source['response_message_id'] = value.get('message_id', '')
        if source['response_conversation_url'] and source['response_message_id']:
            mapping = turn.page.evaluate(CONVERSATION_MAPPING, url.path.rstrip('/').rsplit('/', 1)[-1])
            source['response_position'] = response_position(mapping, source['response_message_id'])
    except Exception:
        # Keep the copied answer even if the page disappears; never attach another turn's URL.
        log.warning('Could not read ChatGPT response source', exc_info=True)
    return source


def _read_answer(page, turn_id: str, on_response_tab=None, on_response_source=None, *, copy_last=False) -> str:
    """Read copied Markdown, never save flattened DOM text as a formatted answer."""
    turn = _turn_locator(page, turn_id)
    source = _response_source(turn, turn_id) if on_response_source else None
    buttons = turn.locator(REPLY_COPY_BUTTON)
    copy_button = buttons.last if copy_last else buttons.first
    try:
        turn.scroll_into_view_if_needed(timeout=5000)
        turn.hover(timeout=5000)
    except Exception:  # noqa: BLE001
        pass

    captured = []
    owns_hook = True
    try:
        installed = page.evaluate(COPY_HOOK)
        if installed is None:
            owns_hook = False
        if not installed:
            raise ChatGPTError("Could not capture ChatGPT's Copy action safely.")
        copy_button.click(timeout=8000)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            captured = page.evaluate(COPY_READ)
            if any(item.get("k") in ("text/plain", "text/markdown") and (item.get("t") or "").strip()
                   for item in captured or []):
                break
            time.sleep(0.1)
    except Exception:  # noqa: BLE001
        log.warning("Could not capture ChatGPT's copied Markdown", exc_info=True)
    finally:
        if owns_hook:
            try:
                captured = page.evaluate(COPY_UNHOOK) or captured
            except Exception:  # noqa: BLE001 - navigating away also destroys the hooks
                log.warning("Could not restore ChatGPT clipboard hooks", exc_info=True)
    for kind in ("text/markdown", "text/plain"):
        for item in captured or []:
            if item.get("k") == kind and (item.get("t") or "").strip():
                cleaned = clean_answer(item['t'])
                if not cleaned.strip():
                    continue
                if on_response_tab:
                    try:
                        title = page.title()
                    except Exception:
                        log.warning('Could not read response tab title', exc_info=True)
                        title = ''
                    on_response_tab(title)
                if on_response_source:
                    on_response_source(source)
                return cleaned
    try:
        title = page.title() if source else ''
    except Exception:
        title = ''
    raise ChatGPTCopyError(
        "ChatGPT answered, but its Markdown could not be copied. "
        "See the Chrome tab and try again. The answer was not saved as unformatted text.", source, title
    )


# --------------------------------------------------------------------------- main


@queued_action
def open_new_chatgpt_tab(return_tab='', cancel_event=None, on_status=None):
    """Open a fresh ordinary ChatGPT tab in the same Chrome used by the app."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        except Exception as exc:
            raise ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
        return _open_new_chatgpt_tab(browser, return_tab, cancel_event, on_status)


@contextmanager
def _request_page(browser, selected_tab, use_current_tab, tab_name, cancel_event=None, on_status=None):
    """Reserve the real Chrome target across native/web workers, not all of Chrome."""
    from .paths import CACHE_DIR
    from .shared_access import ProcessLock
    # Named selection/creation stays atomic. An explicit target can continue
    # chatting while another worker waits to open a new tab.
    with (nullcontext() if selected_tab else ProcessLock(CACHE_DIR / 'chatgpt-request.lock')):
        if selected_tab:
            page, created = _selected_chatgpt_tab(browser, selected_tab)
        elif use_current_tab:
            page, created = _current_chatgpt_tab(browser, cancel_event, on_status)
        else:
            page, created = _dedicated_tab(browser, tab_name, cancel_event, on_status)
        session = page.context.new_cdp_session(page)
        try:
            target = session.send('Target.getTargetInfo')['targetInfo']['targetId']
        finally:
            session.detach()
        # Hash the CDP ID so the lock filename can never contain path components.
        import hashlib
        key = hashlib.sha256(target.encode()).hexdigest()
        lock = ProcessLock(CACHE_DIR / 'chatgpt-tabs' / (key + '.lock'))
        lock.acquire()
    try:
        yield page, created
    finally:
        lock.release()


@queued_action
def ask(
    prompt: str,
    file_path: Path | list[Path] | None = None,
    on_status: Callable[[str], None] = lambda _msg: None,
    new_chat: bool = True,
    tab_name: str = APP_TAB,
    power: int | None = None,
    dry_run: bool = False,
    cancel_event: threading.Event | None = None,
    use_current_tab: bool = False,
    selected_tab: str | None = None,
    on_partial: Callable[[str], None] | None = None,
    on_response_tab: Callable[[str], None] | None = None,
    on_response_source: Callable[[dict], None] | None = None,
    on_sent: Callable[[], None] | None = None,
    upload_timeout_s: float = UPLOAD_TIMEOUT_S,
) -> str:
    """Send ``prompt`` (optionally with one or more files attached) and return the answer (markdown).

    ``power`` sets ChatGPT's Power slider first (index into POWER_LEVELS); None leaves it.
    ``on_partial`` receives growing rendered answer text on this worker thread.
    The returned final answer still comes from the response's Copy button.
    ``on_sent`` runs once on this worker after the submitted turn appears, before
    waiting for the answer, so consumers can durably acknowledge delivered text.

    ``new_chat`` starts a fresh conversation. With ``use_current_tab``, the current ordinary
    ChatGPT tab in Chrome is used; otherwise the dedicated tab ``tab_name`` is used. A tab is
    created if none exists. With ``dry_run`` everything is prepared but nothing is sent.
    """
    from playwright.sync_api import sync_playwright

    if not 1 <= upload_timeout_s <= 1800:
        raise ChatGPTError('Invalid ChatGPT upload timeout.')

    with sync_playwright() as p:
        if cancel_event and cancel_event.is_set():
            raise ChatGPTCancelled(CANCELLED_MESSAGE)
        on_status("Connecting to Chrome…")
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10_000)
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError(
                f"Could not connect to Chrome at {CDP_URL}.\n"
                "Start Chrome with launch_chrome.bat (remote debugging on port 9222) and try again."
            ) from exc

        with _request_page(browser, selected_tab, use_current_tab, tab_name, cancel_event, on_status) as (page, created):
            _check_cancel(cancel_event)
            if new_chat or created:
                on_status("Opening a new ChatGPT chat…")
                page.goto(CHATGPT_URL, wait_until="domcontentloaded")
                if selected_tab:
                    page.evaluate("key => window.name = key", selected_tab)
                elif not use_current_tab:
                    page.evaluate(f"window.name = {tab_name!r}")
            else:
                on_status("Continuing the current ChatGPT chat…")

            _check_context_full(page)
            composer = _visible(page, COMPOSER)
            try:
                composer.wait_for(state="visible", timeout=30_000)
            except Exception as exc:  # noqa: BLE001
                _check_context_full(page)
                raise ChatGPTError(
                    "ChatGPT's message box did not appear. Is this Chrome profile logged in to ChatGPT?"
                ) from exc
            composer_form = _composer_form(composer)
            _check_cancel(cancel_event)
            if not (new_chat or created):
                _wait_not_answering(page, on_status, cancel_event)
            if power is not None:
                on_status("Setting ChatGPT power…")
                on_status(f"Power set to {_set_power(page, power)}")

            attachments = [file_path] if isinstance(file_path, Path) else list(file_path or [])
            upload_deadline = None
            upload_progress = None
            if _clear_attachments(composer_form):
                log.info("Removed leftover attachments from the ChatGPT composer")
            if attachments:
                on_status(f"Uploading {', '.join(p.name for p in attachments)}…")
                file_input = composer_form.locator(FILE_INPUT).first
                if file_input.count() == 0:
                    raise ChatGPTError("Could not find ChatGPT's file upload input (the page layout may have changed).")
                file_input.set_input_files([str(p) for p in attachments])
                started = time.monotonic()
                upload_deadline = started + upload_timeout_s
                upload_progress = _upload_progress(on_status, started, upload_timeout_s)
                _wait_attachments_visible(composer_form, attachments, upload_timeout_s, cancel_event,
                                          on_wait=upload_progress, page=page)

            _check_cancel(cancel_event)
            _check_context_full(page)
            _fill_composer(page, composer, prompt, cancel_event)
            remaining = max(0, upload_deadline - time.monotonic()) if upload_deadline is not None else UPLOAD_TIMEOUT_S
            send = _wait_send_enabled(composer_form, remaining, cancel_event, page=page,
                                      on_wait=upload_progress)
            if dry_run:
                on_status("Dry run: message prepared, not sent")
                return ""

            turns_before = _turn_ids(page)
            answered_before = _answered_turn_ids(page)
            _check_cancel(cancel_event)
            send.click()
            try:
                page.wait_for_function(
                    "(a) => {" + _TURN_ID_JS + """
                       const before = new Set(a.before);
                       return [...document.querySelectorAll(a.turn)].some((t) =>
                         a.attrs.some((x) => t.getAttribute(x) && !before.has(t.getAttribute(x))));
                     }""",
                    arg={**_js_args(), "before": turns_before},
                    timeout=SENT_TIMEOUT_S * 1000,
                )
            except Exception as exc:  # noqa: BLE001
                _check_context_full(page)
                raise ChatGPTError("Clicked send, but the message did not show up in ChatGPT. Check the tab.") from exc
            if on_sent:
                on_sent()
            log.info(
                "Sent message to ChatGPT (%s, attachments %s)",
                "selected Chrome tab" if selected_tab else (
                    "current Chrome tab" if use_current_tab else f"tab {tab_name}"
                ),
                [p.name for p in attachments],
            )

            on_status("Waiting for ChatGPT's answer…")
            try:
                turn_id = _wait_new_answer(
                    page, answered_before, ANSWER_TIMEOUT_S, cancel_event, on_partial, turns_before
                )
            except (ChatGPTCancelled, ChatGPTContextFull, ChatGPTVerificationRequired):
                raise
            except Exception as exc:  # noqa: BLE001
                raise ChatGPTError(
                    f"ChatGPT did not finish answering within {ANSWER_TIMEOUT_S // 60} minutes. See the Chrome tab."
                ) from exc
            _wait_stable(page, turn_id, 60, cancel_event)
            on_status("Reading the answer…")
            return _read_answer(page, turn_id, on_response_tab, on_response_source)
