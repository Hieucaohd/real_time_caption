"""Locate an answer in the complete ChatGPT conversation path, not the visible DOM."""

# Read only the conversation in the selected, already authenticated Chrome tab.
# Credentials remain in the page; return only structure, never tokens or message text.
CONVERSATION_SNAPSHOT = r'''async conversationId => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6000);
    const options = {credentials: 'same-origin', signal: controller.signal, redirect: 'error'};
    try {
        const path = '/backend-api/conversation/' + encodeURIComponent(conversationId);
        let response = await fetch(path, options);
        // ChatGPT also returns 404 for an existing conversation without its bearer session.
        if ([401, 403, 404].includes(response.status)) {
            const sessionResponse = await fetch('/api/auth/session', options);
            if (!sessionResponse.ok) return null;
            const session = await sessionResponse.json();
            if (!session.accessToken) return null;
            response = await fetch(path, {...options, headers: {Authorization: 'Bearer ' + session.accessToken}});
        }
        if (!response.ok) return null;
        const data = await response.json();
        if (!data.mapping || typeof data.mapping !== 'object') return null;
        const mapping = Object.fromEntries(Object.entries(data.mapping).map(([id, node]) => {
            const m = node.message;
            return [id, {parent: node.parent, message: m ? {
                id: m.id, author: {role: m.author?.role}, channel: m.channel,
                recipient: m.recipient, content: {content_type: m.content?.content_type},
                metadata: {is_visually_hidden_from_conversation: m.metadata?.is_visually_hidden_from_conversation}
            } : null}];
        }));
        return {mapping, current_node: data.current_node};
    } catch { return null; }
    finally { clearTimeout(timer); }
}'''
CONVERSATION_MAPPING = 'async id => (await (' + CONVERSATION_SNAPSHOT + ')(id))?.mapping ?? null'


def is_answer(message):
    return (message.get('author', {}).get('role') == 'assistant'
            and message.get('channel') in (None, 'final')
            and message.get('recipient') in (None, 'all')
            and not (message.get('metadata') or {}).get('is_visually_hidden_from_conversation')
            and message.get('content', {}).get('content_type') not in ('thoughts', 'reasoning_recap'))


def conversation_path(mapping, key):
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    seen, path = set(), []
    while key is not None:
        if key in seen or key not in mapping:
            return None
        seen.add(key)
        node = mapping[key]
        path.append(node.get('message') or {})
        if 'parent' not in node:
            return None
        key = node['parent']
    return list(reversed(path))


def response_message_at_position(snapshot, position):
    """Resolve the user's ordinal on the conversation's active history path."""
    if type(position) is not int or position < 1:
        raise ValueError('Cần link hội thoại và số thứ tự câu trả lời từ 1 để tạo nhánh ChatGPT.')
    if not isinstance(snapshot, dict):
        raise ValueError('Không lấy được lịch sử đầy đủ của hội thoại ChatGPT. Chưa tạo nhánh.')
    path = conversation_path(snapshot.get('mapping'), snapshot.get('current_node'))
    if path is None:
        raise ValueError('Không lấy được lịch sử đầy đủ của hội thoại ChatGPT. Chưa tạo nhánh.')
    answers = [message for message in path if is_answer(message)]
    if position > len(answers):
        raise ValueError(f'Hội thoại có {len(answers)} câu trả lời; không có câu trả lời thứ {position}. Hãy cập nhật nguồn của node cha.')
    message_id = answers[position - 1].get('id')
    if not message_id:
        raise ValueError('Không xác định được response để tạo nhánh ChatGPT.')
    return message_id


def response_position(mapping, message_id):
    """1-based assistant answer index along this response's branch; unknown => None.

    Exclude system/user/tool messages, hidden messages and intermediate reasoning or
    tool calls. Follow parents, so regenerated alternatives are never double counted.
    Require a complete path to the root: a partial history cannot give a safe index.
    """
    if not isinstance(mapping, dict) or not message_id:
        return None

    key = next((key for key, node in mapping.items()
                if (node.get('message') or {}).get('id') == message_id), None)
    if key is None or not is_answer(mapping[key]['message']):
        return None
    path = conversation_path(mapping, key)
    return sum(is_answer(message) for message in path) if path is not None else None
