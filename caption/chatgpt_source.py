"""Locate an answer in the complete ChatGPT conversation path, not the visible DOM."""

# Read only the conversation in the selected, already authenticated Chrome tab.
# Credentials remain in the page; return only structure, never tokens or message text.
CONVERSATION_MAPPING = r'''async conversationId => {
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
        return Object.fromEntries(Object.entries(data.mapping).map(([id, node]) => {
            const m = node.message;
            return [id, {parent: node.parent, message: m ? {
                id: m.id, author: {role: m.author?.role}, channel: m.channel,
                recipient: m.recipient, content: {content_type: m.content?.content_type},
                metadata: {is_visually_hidden_from_conversation: m.metadata?.is_visually_hidden_from_conversation}
            } : null}];
        }));
    } catch { return null; }
    finally { clearTimeout(timer); }
}'''


def response_position(mapping, message_id):
    """1-based assistant answer index along this response's branch; unknown => None.

    Exclude system/user/tool messages, hidden messages and intermediate reasoning or
    tool calls. Follow parents, so regenerated alternatives are never double counted.
    Require a complete path to the root: a partial history cannot give a safe index.
    """
    if not isinstance(mapping, dict) or not message_id:
        return None

    def is_answer(message):
        return (message.get('author', {}).get('role') == 'assistant'
                and message.get('channel') in (None, 'final')
                and message.get('recipient') in (None, 'all')
                and not (message.get('metadata') or {}).get('is_visually_hidden_from_conversation')
                and message.get('content', {}).get('content_type') not in ('thoughts', 'reasoning_recap'))

    key = next((key for key, node in mapping.items()
                if (node.get('message') or {}).get('id') == message_id), None)
    if key is None or not is_answer(mapping[key]['message']):
        return None
    seen, position = set(), 0
    while key is not None:
        if key in seen or key not in mapping:
            return None
        seen.add(key)
        node = mapping[key]
        position += int(is_answer(node.get('message') or {}))
        # Missing parent is not proof that this is the start of the history.
        if 'parent' not in node:
            return None
        key = node['parent']
    return position or None
