"""Branch a saved response through ChatGPT's UI and identify the new Chrome tab."""
import re
import logging
import time
import uuid
from dataclasses import asdict
from urllib.parse import unquote, urlsplit

from . import chatgpt
from .chatgpt_source import CONVERSATION_SNAPSHOT, conversation_path, response_message_at_position

CHATGPT_HOSTS = ('chatgpt.com', 'www.chatgpt.com', 'chat.openai.com')
BRANCH_TIMEOUT = 45
log = logging.getLogger(__name__)


def _tab_snapshot(browser):
    """Stable CDP target IDs distinguish duplicate titles, source tabs and popups."""
    result = {}
    for context in browser.contexts:
        for page in context.pages:
            if urlsplit(page.url).hostname not in CHATGPT_HOSTS:
                continue
            session = None
            try:
                if chatgpt.is_dictation_tab(page.evaluate('window.name')):
                    continue
                session = context.new_cdp_session(page)
                target_id = session.send('Target.getTargetInfo')['targetInfo']['targetId']
                result[target_id] = {'page': page, 'url': page.url}
            except Exception:
                pass  # A tab can close while scanning.
            finally:
                if session:
                    try:
                        session.detach()
                    except Exception:
                        pass
    return result


SCROLL_TOWARD_RESPONSE = r'''({ranks, target}) => {
    const units = [...document.querySelectorAll('[data-message-id], [data-chatgpt-search-message-ids]')]
        .filter(el => el.getClientRects().length);
    const visibleRanks = units.flatMap(el => (el.getAttribute('data-message-id') ||
        el.getAttribute('data-chatgpt-search-message-ids') || '').trim().split(/\s+/))
        .map(id => ranks[id]).filter(rank => rank !== undefined);
    let host = units.at(-1);
    while (host && !(host.scrollHeight > host.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(host).overflowY))) host = host.parentElement;
    host ??= document.querySelector('[data-app-action-timeline-scroll]') || document.scrollingElement;
    if (!host) return;
    const up = visibleRanks.length && target < Math.min(...visibleRanks);
    host.scrollBy({top: (up ? -1 : 1) * Math.max(150, host.clientHeight * .75), behavior: 'instant'});
}'''


def _find_response_turn(page, snapshot, message_id):
    quoted = chatgpt._css_string(message_id)
    unit = page.locator(f'[data-message-author-role="assistant"][data-message-id={quoted}], '
                        f'[data-chatgpt-search-message-ids~={quoted}]').filter(visible=True).last
    path = conversation_path(snapshot['mapping'], snapshot['current_node'])
    ranks = {message['id']: index for index, message in enumerate(path) if message.get('id')}
    deadline = time.monotonic() + BRANCH_TIMEOUT
    while time.monotonic() < deadline:
        if unit.count():
            turn = unit.locator('xpath=ancestor-or-self::*[@data-turn-key or starts-with(@data-testid,"conversation-turn-")][1]')
            if turn.count():
                turn.scroll_into_view_if_needed(timeout=5000)
                turn.hover(timeout=5000)
                return turn
        page.evaluate(SCROLL_TOWARD_RESPONSE, {'ranks': ranks, 'target': ranks[message_id]})
        page.wait_for_timeout(200)
    raise chatgpt.ChatGPTError('Không tìm thấy câu trả lời đã lưu trên giao diện ChatGPT.')


def _branch_menu_item(page, turn):
    turn.get_by_role('button', name=re.compile(r'^(More actions|More|Thêm|Tùy chọn khác|Hành động khác)$', re.I)).click(timeout=8000)
    final = page.get_by_role('menuitem', name=re.compile(r'^(Branch in new chat|Tạo nhánh trong cuộc trò chuyện mới|Phân nhánh trong cuộc trò chuyện mới)$', re.I)).filter(visible=True).last
    if not final.count():
        parent = page.get_by_role('menuitem', name=re.compile(r'^(Branch conversation|Tạo nhánh cuộc trò chuyện|Phân nhánh cuộc trò chuyện)$', re.I)).filter(visible=True).last
        parent.hover(timeout=8000)
        try:
            final.wait_for(state='visible', timeout=1500)
        except Exception:
            parent.click(timeout=5000)
    final.wait_for(state='visible', timeout=8000)
    return final


def _new_branch_tab(before, before_click, after, helper, conversation_id, confirmed_page=None):
    candidates = []
    for target_id, tab in after.items():
        if target_id in before or (target_id in before_click and tab['page'] != helper):
            continue
        match = re.search(r'/c/([^/]+)/?$', urlsplit(tab['url']).path)
        if not match:
            continue
        route_id = unquote(match[1])
        remote_match = bool(conversation_id) and route_id == conversation_id
        # Native branches can stay on a local-chatgpt: route until the next user message.
        # Accept that route only on the page receiving the successful, exact-source branch request.
        local_match = (tab['page'] == confirmed_page
                       and re.fullmatch(r'local-chatgpt:[A-Za-z0-9_-]+', route_id) is not None)
        if remote_match or local_match:
            candidates.append(tab['page'])
    return candidates[0] if len(candidates) == 1 else None


def source_conversation_id(url, position):
    parsed = urlsplit(url)
    match = re.search(r'/c/([A-Za-z0-9_-]+)/?$', parsed.path)
    if (parsed.scheme != 'https' or parsed.hostname not in ('chatgpt.com', 'www.chatgpt.com', 'chat.openai.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443) or not match
            or type(position) is not int or position < 1):
        raise ValueError('Hãy cập nhật link hội thoại ChatGPT và số thứ tự câu trả lời ở Nguồn ChatGPT của node.')
    return match[1]


def _branch_in_browser(browser, url, position, return_tab=''):
    with chatgpt._return_to_web_tab(browser, return_tab) as focus:
        tab = _branch_in_browser_impl(browser, url, position)
    if focus['restored']:
        tab['focused'] = False
    return tab


def _branch_in_browser_impl(browser, url, position):
    source_id = source_conversation_id(url, position)
    if not browser.contexts:
        raise chatgpt.ChatGPTError('Chrome chưa có cửa sổ để mở nhánh ChatGPT.')
    before = _tab_snapshot(browser)
    page = chatgpt._open_background_tab(browser)
    branch_requested = False
    responses = []
    listener = None
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        page.bring_to_front()
        chatgpt._visible(page, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
        snapshot = page.evaluate(CONVERSATION_SNAPSHOT, source_id)
        message_id = response_message_at_position(snapshot, position)
        turn = _find_response_turn(page, snapshot, message_id)
        button = _branch_menu_item(page, turn)
        before_click = _tab_snapshot(browser)

        def listener(response):
            if response.request.method != 'POST' or urlsplit(response.url).path != '/backend-api/conversation/new_branch':
                return
            try:
                payload = response.request.post_data_json
                if payload.get('conversation_id') == source_id and payload.get('message_id') == message_id:
                    responses.append((response, response.frame.page))
            except Exception:
                pass

        # The native menu can open a popup; listen on the context, not just the helper tab.
        page.context.on('response', listener)
        from .chatgpt_tab_gate import opening as tab_opening
        with tab_opening(browser) as opening:
            branch_requested = True
            button.click(timeout=8000)
            deadline = time.monotonic() + BRANCH_TIMEOUT
            while not responses and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if not responses:
                raise chatgpt.ChatGPTError('Chưa nhận được xác nhận tạo nhánh từ ChatGPT.')
            response, response_page = responses[0]
            if not response.ok:
                raise chatgpt.ChatGPTError('ChatGPT không tạo được nhánh. Hãy kiểm tra tab mới.')
            try:
                conversation = response.json().get('conversation') or {}
                new_id = conversation.get('conversation_id') or conversation.get('id')
            except Exception:
                # A full navigation can discard the CDP response body. In that case,
                # only the page that received this exact successful branch response may identify the result.
                new_id = None
            if new_id == source_id:
                # A branch draft may still carry the source server ID. Its confirmed local route
                # identifies the draft; the unchanged source tab must never be selected.
                new_id = None
            target = None
            while time.monotonic() < deadline:
                after = _tab_snapshot(browser)
                if not new_id:
                    match = re.search(r'/c/([A-Za-z0-9_-]+)/?$', urlsplit(response_page.url).path)
                    if match and match[1] != source_id and any(tab['page'] == response_page for tab in after.values()):
                        new_id = match[1]
                target = _new_branch_tab(before, before_click, after, page, new_id, response_page)
                if target:
                    break
                page.wait_for_timeout(100)
            if target is None:
                raise chatgpt.ChatGPTError('Đã bấm tạo nhánh nhưng chưa xác định được tab chứa hội thoại mới.')
            chatgpt._visible(target, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
            with chatgpt._TAB_SCAN_LOCK:
                key = target.evaluate('([prefix, proposed]) => { if (!window.name.startsWith(prefix)) window.name = proposed; return window.name; }',
                                      [chatgpt.CHAT_TAB_PREFIX, chatgpt.CHAT_TAB_PREFIX + uuid.uuid4().hex])
            opening.opened(target)
            target.bring_to_front()
            title = target.title().strip() or 'ChatGPT branch'
            return asdict(chatgpt.ChromeChatGPTTab(key=key, label=f'{title} — {key.removeprefix(chatgpt.CHAT_TAB_PREFIX)[:8]}',
                                                 title=title, url=target.url, focused=True))
    except Exception as exc:
        if not branch_requested:
            try:
                page.close()  # Only our newly opened, still unbranched tab.
            except Exception:
                pass  # The user may already have closed it.
            if isinstance(exc, (ValueError, chatgpt.ChatGPTError)):
                raise
            raise chatgpt.ChatGPTError('Không đọc được hội thoại nguồn. Kiểm tra link và đăng nhập ChatGPT trên Chrome 9222.') from exc
        # Do not click again automatically: a delayed result could create a duplicate.
        log.warning('Could not confirm the ChatGPT branch tab after the UI click', exc_info=True)
        raise chatgpt.ChatGPTError('Chưa xác nhận được nhánh ChatGPT đã sẵn sàng. Tab mới vẫn mở; hãy kiểm tra tab đó và Refresh danh sách tab trước khi thử lại.') from exc
    finally:
        if listener:
            page.context.remove_listener('response', listener)


def create_branch(url, position, return_tab=''):
    source_conversation_id(url, position)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=10_000)
        except Exception as exc:
            raise chatgpt.ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
        return _branch_in_browser(browser, url, position, return_tab)
