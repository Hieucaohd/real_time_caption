"""Open a saved ChatGPT response in a new tab without sending or branching."""
import uuid
from dataclasses import asdict

from . import chatgpt, paths
from .chatgpt_branch import _find_response_turn, source_conversation_id
from .chatgpt_source import CONVERSATION_SNAPSHOT, response_message_at_position
from .shared_access import ProcessLock


def _open_response_in_browser(browser, url, position):
    source_id = source_conversation_id(url, position)
    if not browser.contexts:
        raise chatgpt.ChatGPTError('Chrome chưa có cửa sổ để mở hội thoại ChatGPT.')
    # Only tab creation is serialized. Reading a response must not block other tabs' work.
    with ProcessLock(paths.CACHE_DIR / 'chatgpt-request.lock'):
        page = chatgpt._open_background_tab(browser)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        chatgpt.require_page_login(page)
        page.bring_to_front()
        with chatgpt._TAB_SCAN_LOCK:
            key = page.evaluate('([prefix, proposed]) => { if (!window.name.startsWith(prefix)) window.name = proposed; return window.name; }',
                                [chatgpt.CHAT_TAB_PREFIX, chatgpt.CHAT_TAB_PREFIX + uuid.uuid4().hex])
        chatgpt._visible(page, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
        snapshot = page.evaluate(CONVERSATION_SNAPSHOT, source_id)
        message_id = response_message_at_position(snapshot, position)
        turn = _find_response_turn(page, snapshot, message_id)
        quoted = chatgpt._css_string(message_id)
        # Align the answer itself, rather than the user question at the start of its turn.
        answer = turn.locator(f'[data-message-author-role="assistant"][data-message-id={quoted}], '
                              f'[data-chatgpt-search-message-ids~={quoted}]').filter(visible=True).last
        answer.evaluate("el => el.scrollIntoView({block: 'start', behavior: 'instant'})")
        title = page.title().strip() or 'ChatGPT'
        return {**asdict(chatgpt.ChromeChatGPTTab(key=key, label=title, title=title, url=page.url, focused=True)),
                'response_position': position}
    except (chatgpt.ChatGPTLoginRequired, chatgpt.ChatGPTLoginUnverified, chatgpt.ChatGPTVerificationRequired):
        raise
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (ValueError, chatgpt.ChatGPTError)) else 'Kiểm tra kết nối và đăng nhập ChatGPT trên Chrome 9222.'
        raise chatgpt.ChatGPTError('Tab mới đã mở nhưng chưa cuộn được tới câu trả lời. ' + reason) from exc


def open_response(url, position):
    source_conversation_id(url, position)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=10_000)
        except Exception as exc:
            raise chatgpt.ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
        return _open_response_in_browser(browser, url, position)
