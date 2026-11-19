"""Copy an existing answer again; never submit a prompt or regenerate an answer."""
import logging
from . import chatgpt, paths
from .chatgpt_branch import _find_response_turn, source_conversation_id
from .chatgpt_source import CONVERSATION_SNAPSHOT, conversation_path, is_answer, response_message_at_position, response_position
from .shared_access import ProcessLock


def latest_answer_for_question(snapshot, position):
    anchor = response_message_at_position(snapshot, position)
    messages = conversation_path(snapshot['mapping'], snapshot['current_node'])
    # The user-editable URL and ordinal are authoritative, including old answers.
    ids = [message.get('id') for message in messages]
    index = ids.index(anchor)
    latest = anchor
    for message in messages[index + 1:]:
        if message.get('author', {}).get('role') == 'user':
            break
        if is_answer(message):
            latest = message['id']
    return latest


def _recopy_in_browser(browser, url, position, return_tab=''):
    conversation = source_conversation_id(url, position)
    if not browser.contexts:
        raise chatgpt.ChatGPTError('Chrome chưa có cửa sổ để đọc lại hội thoại ChatGPT.')
    with chatgpt._return_to_web_tab(browser, return_tab):
        with ProcessLock(paths.CACHE_DIR / 'chatgpt-request.lock'):
            page = chatgpt._open_background_tab(browser)
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=30_000)
            chatgpt.require_page_login(page)
            page.bring_to_front()
            chatgpt._visible(page, chatgpt.COMPOSER).wait_for(state='visible', timeout=30_000)
            snapshot = page.evaluate(CONVERSATION_SNAPSHOT, conversation)
            target = latest_answer_for_question(snapshot, position)
            turn = _find_response_turn(page, snapshot, target)
            turn_id = turn.evaluate('(turn, a) => {' + chatgpt._TURN_ID_JS + 'return turnId(turn, a);}', chatgpt._js_args())
            if not turn_id:
                raise chatgpt.ChatGPTError('Không xác định được câu trả lời để copy lại.')
            source, title = {}, {}
            content = chatgpt._read_answer(page, turn_id, lambda value: title.update(value=value), source.update, copy_last=True)
            if not content.strip():
                raise chatgpt.ChatGPTError('Câu trả lời trên ChatGPT vẫn rỗng. Nội dung đã lưu chưa thay đổi.')
            return dict(content=content, response_tab_title=title.get('value', ''),
                        response_conversation_url=source.get('response_conversation_url') or url, response_turn_id=turn_id,
                        response_message_id=source.get('response_message_id') or target,
                        response_position=source.get('response_position') or response_position(snapshot['mapping'], target))
        finally:
            try:
                page.close()
            except Exception:
                logging.getLogger(__name__).debug('Temporary response tab already closed', exc_info=True)


def recopy_response(url, position, return_tab=''):
    source_conversation_id(url, position)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(chatgpt.CDP_URL, timeout=10_000)
        except Exception as exc:
            raise chatgpt.ChatGPTError('Không kết nối được Chrome 9222. Hãy chạy launch_chrome.bat.') from exc
        return _recopy_in_browser(browser, url, position, return_tab)
