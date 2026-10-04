"""Ask ChatGPT about a transcript file through the user's logged-in Chrome.

Chrome must be running with ``--remote-debugging-port=9222`` (see
``C:\\Users\\ADMIN\\ai-orchestrator\\launch_chrome.bat``). We attach over CDP with
Playwright, attach files, type the selected prompt, send, wait for the answer and
return it as markdown. Chat and Summary can use the user's current ChatGPT tab;
New words keeps a dedicated tab marked via ``window.name``.

Waiting/reading follows ai-orchestrator's ChatGPT adapter: remember which turns
already have a finished answer, then wait for a *new* turn whose "Copy" button has
appeared (it only shows once the answer is complete), and read the markdown by
clicking that button while the page's clipboard calls are intercepted.

Selectors are the part most likely to break when ChatGPT changes its UI.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

CDP_URL = "http://localhost:9222"
CHATGPT_URL = "https://chatgpt.com/"
# Legacy/default dedicated tab name. Chat and Summary now request the current ordinary
# ChatGPT tab; New words still uses its own named tab.
APP_TAB = "real-time-caption-summary"
VOCAB_TAB = "real-time-caption-vocab"
CHAT_TAB_PREFIX = "real-time-caption-chat:"
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

# Intercept the page's clipboard writes so the Copy button hands us the markdown
# without touching the user's real clipboard (same trick as ai-orchestrator).
COPY_HOOK = """() => {
  window.__rtcCopy = [];
  const c = navigator.clipboard;
  window.__rtcOrig = {wt: c.writeText, w: c.write};
  c.writeText = async (t) => { window.__rtcCopy.push({k: 'text/plain', t: t}); };
  c.write = async (items) => {
    for (const it of items) for (const k of it.types) {
      try { window.__rtcCopy.push({k: k, t: await (await it.getType(k)).text()}); } catch (e) {}
    }
  };
  window.__rtcListen = (e) => {
    if (e.clipboardData) for (const k of e.clipboardData.types || [])
      window.__rtcCopy.push({k: k, t: e.clipboardData.getData(k)});
  };
  document.addEventListener('copy', window.__rtcListen, true);
}"""

COPY_UNHOOK = """() => {
  const c = navigator.clipboard;
  if (window.__rtcOrig) { c.writeText = window.__rtcOrig.wt; c.write = window.__rtcOrig.w; }
  document.removeEventListener('copy', window.__rtcListen, true);
  const got = window.__rtcCopy || [];
  window.__rtcCopy = null; window.__rtcOrig = null;
  return got;
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


CANCELLED_MESSAGE = "Cancelled by user. You can press Retry to send the same request again."


class ChatGPTCancelled(ChatGPTError):
    pass


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


def _dedicated_tab(browser, tab_name: str):
    """Reuse our dedicated tab if it is still open, otherwise open a new one.

    Returns ``(page, created)``.
    """
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
    return _open_background_tab(browser), True


def _current_chatgpt_tab(browser):
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
                if page.evaluate("window.name") == VOCAB_TAB:
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
    return _open_background_tab(browser), True


def _selected_chatgpt_tab(browser, tab_key: str):
    """Return the ordinary ChatGPT tab carrying ``tab_key`` or fail without opening another."""
    for context in browser.contexts:
        for page in context.pages:
            if "chatgpt.com" not in page.url:
                continue
            try:
                if page.evaluate("window.name") == tab_key:
                    return page, False
            except Exception:  # noqa: BLE001 - tab closing or crashed
                continue
    raise ChatGPTError("The selected ChatGPT tab is no longer open. Press Refresh and choose another tab.")


def list_chrome_chatgpt_tabs() -> list[ChromeChatGPTTab]:
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
                    if key == VOCAB_TAB:
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


def _open_background_tab(browser):
    """Open a tab without switching Chrome to it (Playwright's new_page() activates the tab)."""
    context = browser.contexts[0]
    try:
        session = browser.new_browser_cdp_session()
        with context.expect_page(timeout=10_000) as new_page:
            session.send("Target.createTarget", {"url": "about:blank", "background": True})
        session.detach()
        return new_page.value
    except Exception:  # noqa: BLE001
        log.warning("Could not open a background tab; falling back to a normal one", exc_info=True)
        return context.new_page()


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


def _wait_not_answering(page, on_status: Callable[[str], None]) -> None:
    """When continuing a conversation, let ChatGPT finish its previous answer first."""
    stop = _visible(page, STOP_BUTTON)
    try:
        if stop.count() == 0 or not stop.is_visible():
            return
        on_status("Waiting for ChatGPT to finish its previous answer…")
        stop.wait_for(state="hidden", timeout=ANSWER_TIMEOUT_S * 1000)
    except Exception as exc:  # noqa: BLE001
        raise ChatGPTError("ChatGPT is still answering the previous message. Try again when it finishes.") from exc


def _set_power(page, level: int) -> str:
    """Move the model picker's Power slider to ``level`` (0 = lowest); returns ChatGPT's label for it."""
    picker = _visible(page, MODEL_PICKER)
    if picker.count() == 0:
        raise ChatGPTError("Could not find ChatGPT's model picker to set the power level.")
    picker.click()
    try:
        power = _visible(page, POWER_ITEM)
        try:
            power.wait_for(state="visible", timeout=5000)
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError("ChatGPT's model picker has no Power slider (the layout may have changed).") from exc
        slider = _visible(page, POWER_SLIDER)
        power.focus()
        target = max(0, min(level, int(slider.get_attribute("aria-valuemax") or len(POWER_LEVELS) - 1)))
        for _ in range(len(POWER_LEVELS) + 2):
            current = int(slider.get_attribute("aria-valuenow") or 0)
            if current == target:
                break
            page.keyboard.press("ArrowRight" if current < target else "ArrowLeft")
            page.wait_for_timeout(250)
        if int(slider.get_attribute("aria-valuenow") or -1) != target:
            raise ChatGPTError("Could not move ChatGPT's Power slider to the chosen level.")
        # Status reads e.g. "Medium, 2 of 3."
        return _visible(page, POWER_STATUS).inner_text().split(",")[0].strip()
    finally:
        page.keyboard.press("Escape")


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


def _wait_attachments_visible(form, attachments: list[Path], timeout_s: float) -> None:
    """Do not send until every selected file appears in the visible composer's chips."""
    expected = {path.name for path in attachments}
    deadline = time.monotonic() + timeout_s
    remove_buttons = form.locator("button[aria-label^='Remove ']")
    while time.monotonic() < deadline:
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


def _wait_send_enabled(scope, timeout_s: float):
    """The send button stays disabled until the attachment has finished uploading."""
    deadline = time.monotonic() + timeout_s
    button = _visible(scope, SEND_BUTTON)
    while time.monotonic() < deadline:
        try:
            if button.is_visible() and button.is_enabled():
                return button
        except Exception:  # noqa: BLE001 - button re-rendered between checks
            pass
        time.sleep(0.5)
    raise ChatGPTError(
        "ChatGPT's send button never became available — the file upload may have failed. "
        "Check the ChatGPT tab."
    )


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
    page, known: list[str], timeout_s: float, cancel_event: threading.Event | None = None
) -> str:
    """Wait until a turn that wasn't answered before gets its Copy button; return its id."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _cancel_if_requested(page, cancel_event)
        try:
            handle = page.wait_for_function(
                "(a) => {" + _TURN_ID_JS + """
                   const known = new Set(a.known);
                   const buttons = [...document.querySelectorAll(a.copy)];
                   for (let i = buttons.length - 1; i >= 0; i--) {
                     const id = turnId(buttons[i], a);
                     if (id && !known.has(id)) return id;
                   }
                   return false;
                 }""",
                arg={**_js_args(), "known": known},
                timeout=750,
                polling=150,
            )
            return handle.json_value()
        except PlaywrightTimeoutError:
            continue
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
        n = _answer_text_length(page, turn_id)
        now = time.monotonic()
        if n != last:
            last, since = n, now
        elif now - since >= QUIET_S:
            return
        time.sleep(0.4)


def _read_answer(page, turn_id: str) -> str:
    """Markdown via the answer's Copy button; falls back to the rendered text."""
    turn = _turn_locator(page, turn_id)
    copy_button = turn.locator(REPLY_COPY_BUTTON).first
    try:
        turn.scroll_into_view_if_needed(timeout=5000)
        turn.hover(timeout=5000)
    except Exception:  # noqa: BLE001
        pass

    page.evaluate(COPY_HOOK)
    try:
        copy_button.click(timeout=8000)
        time.sleep(0.6)  # give the page time to call the clipboard API
    except Exception:  # noqa: BLE001
        log.warning("Could not click ChatGPT's Copy button", exc_info=True)
    finally:
        captured = page.evaluate(COPY_UNHOOK)
    for item in captured or []:
        if item.get("k") == "text/plain" and (item.get("t") or "").strip():
            return clean_answer(item["t"])

    # Fallback: rendered text of the answer part of the turn (loses markdown formatting).
    try:
        text = copy_button.evaluate("(b) => (b.closest('.group') || b.parentElement).innerText", timeout=5000)
    except Exception:  # noqa: BLE001
        text = ""
    if text and text.strip():
        return clean_answer(text)
    raise ChatGPTError("ChatGPT answered, but the answer could not be read. See the Chrome tab.")


# --------------------------------------------------------------------------- main


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
) -> str:
    """Send ``prompt`` (optionally with one or more files attached) and return the answer (markdown).

    ``power`` sets ChatGPT's Power slider first (index into POWER_LEVELS); None leaves it.

    ``new_chat`` starts a fresh conversation. With ``use_current_tab``, the current ordinary
    ChatGPT tab in Chrome is used; otherwise the dedicated tab ``tab_name`` is used. A tab is
    created if none exists. With ``dry_run`` everything is prepared but nothing is sent.
    """
    from playwright.sync_api import sync_playwright

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

        # Everything runs in the background: Chrome is never brought to the front.
        if selected_tab:
            page, created = _selected_chatgpt_tab(browser, selected_tab)
        elif use_current_tab:
            page, created = _current_chatgpt_tab(browser)
        else:
            page, created = _dedicated_tab(browser, tab_name)
        if new_chat or created:
            on_status("Opening a new ChatGPT chat…")
            page.goto(CHATGPT_URL, wait_until="domcontentloaded")
            if selected_tab:
                page.evaluate("key => window.name = key", selected_tab)
            elif not use_current_tab:
                page.evaluate(f"window.name = {tab_name!r}")
        else:
            on_status("Continuing the current ChatGPT chat…")

        composer = _visible(page, COMPOSER)
        try:
            composer.wait_for(state="visible", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError(
                "ChatGPT's message box did not appear. Is this Chrome profile logged in to ChatGPT?"
            ) from exc
        composer_form = _composer_form(composer)
        if not (new_chat or created):
            _wait_not_answering(page, on_status)
        if power is not None:
            on_status("Setting ChatGPT power…")
            on_status(f"Power set to {_set_power(page, power)}")

        attachments = [file_path] if isinstance(file_path, Path) else list(file_path or [])
        if _clear_attachments(composer_form):
            log.info("Removed leftover attachments from the ChatGPT composer")
        if attachments:
            on_status(f"Uploading {', '.join(p.name for p in attachments)}…")
            file_input = composer_form.locator(FILE_INPUT).first
            if file_input.count() == 0:
                raise ChatGPTError("Could not find ChatGPT's file upload input (the page layout may have changed).")
            file_input.set_input_files([str(p) for p in attachments])
            _wait_attachments_visible(composer_form, attachments, UPLOAD_TIMEOUT_S)

        composer.click()
        composer.fill(prompt)
        send = _wait_send_enabled(composer_form, UPLOAD_TIMEOUT_S)
        if dry_run:
            on_status("Dry run: message prepared, not sent")
            return ""

        turns_before = _turn_ids(page)
        answered_before = _answered_turn_ids(page)
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
            raise ChatGPTError("Clicked send, but the message did not show up in ChatGPT. Check the tab.") from exc
        log.info(
            "Sent message to ChatGPT (%s, attachments %s)",
            "selected Chrome tab" if selected_tab else (
                "current Chrome tab" if use_current_tab else f"tab {tab_name}"
            ),
            [p.name for p in attachments],
        )

        on_status("Waiting for ChatGPT's answer…")
        try:
            turn_id = _wait_new_answer(page, answered_before, ANSWER_TIMEOUT_S, cancel_event)
        except ChatGPTCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ChatGPTError(
                f"ChatGPT did not finish answering within {ANSWER_TIMEOUT_S // 60} minutes. See the Chrome tab."
            ) from exc
        _wait_stable(page, turn_id, 60, cancel_event)
        on_status("Reading the answer…")
        return _read_answer(page, turn_id)
