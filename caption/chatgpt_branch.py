"""Open ChatGPT's native Branch in new chat route for a saved response ordinal."""
import re
import uuid
from dataclasses import asdict
from urllib.parse import quote, urlsplit

from . import chatgpt
from .chatgpt_source import CONVERSATION_SNAPSHOT, response_message_at_position


def source_conversation_id(url, position):
    parsed = urlsplit(url)
    match = re.search(r'/c/([A-Za-z0-9_-]+)/?$', parsed.path)
    if (parsed.scheme != 'https' or parsed.hostname not in ('chatgpt.com', 'www.chatgpt.com', 'chat.openai.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443) or not match
            or type(position) is not int or position < 1):
        raise ValueError('Hãy cập nhật link hội thoại ChatGPT và số thứ tự câu trả lời ở Nguồn ChatGPT của node cha.')
    return match[1]


def _branch_in_browser(browser, url, position):
    source_id = source_conversation_id(url, position)
    if not browser.contexts:
        raise chatgpt.ChatGPTError('Chrome chưa có cửa sổ để mở nhánh ChatGPT.')
    page = chatgpt._open_background_tab(browser)
    branch_requested = False
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        chatgpt._visible(page, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
        snapshot = page.evaluate(CONVERSATION_SNAPSHOT, source_id)
        message_id = response_message_at_position(snapshot, position)
        # This is ChatGPT's own branch route, also used by its native menu.
        branch_url = f'https://chatgpt.com/branch/{quote(source_id, safe="")}/{quote(message_id, safe="")}'
        branch_requested = True
        with page.expect_response(lambda response: response.request.method == 'POST'
                                  and urlsplit(response.url).path == '/backend-api/conversation/new_branch', timeout=45_000) as created:
            page.goto(branch_url, wait_until='domcontentloaded', timeout=30_000)
        response = created.value
        if not response.ok:
            raise chatgpt.ChatGPTError('ChatGPT không tạo được nhánh. Hãy kiểm tra tab mới.')
        conversation = response.json().get('conversation') or {}
        new_id = conversation.get('conversation_id') or conversation.get('id')
        if not new_id or new_id == source_id:
            raise chatgpt.ChatGPTError('ChatGPT chưa trả về hội thoại nhánh mới.')
        page.wait_for_function(r'''newId => {
            const match = location.pathname.match(/\/c\/([^/]+)\/?$/);
            return location.hostname === 'chatgpt.com' && match && match[1] === newId;
        }''', arg=new_id, timeout=45_000)
        chatgpt._visible(page, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
        key = chatgpt.CHAT_TAB_PREFIX + uuid.uuid4().hex
        page.evaluate('key => window.name = key', key)
        page.bring_to_front()
        title = page.title().strip() or 'ChatGPT branch'
        return asdict(chatgpt.ChromeChatGPTTab(key=key, label=f'{title} — {key.removeprefix(chatgpt.CHAT_TAB_PREFIX)[:8]}',
                                             title=title, url=page.url, focused=True))
    except Exception as exc:
        if not branch_requested:
            try:
                page.close()  # Only our newly opened, still unbranched tab.
            except Exception:
                pass  # The user may already have closed it.
            if isinstance(exc, ValueError):
                raise
            raise chatgpt.ChatGPTError('Không đọc được hội thoại nguồn. Kiểm tra link và đăng nhập ChatGPT trên Chrome 9222.') from exc
        # Do not retry the branch route: a delayed result could create a duplicate.
        raise chatgpt.ChatGPTError('Chưa xác nhận được nhánh ChatGPT đã sẵn sàng. Tab mới vẫn mở; hãy kiểm tra tab đó và Refresh danh sách tab trước khi thử lại.') from exc


@chatgpt._shared_request
def create_branch(url, position):
    source_conversation_id(url, position)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=10_000)
        except Exception as exc:
            raise chatgpt.ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
        return _branch_in_browser(browser, url, position)
