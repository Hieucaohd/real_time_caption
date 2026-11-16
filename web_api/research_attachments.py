"""Read node attachments from their frozen SQLite snapshots, scoped to the conversation."""
import mimetypes
from pathlib import Path


def _entries(service, cid, nid):
    node = service.research.store.get(cid, nid, private=True)
    request = node['request']
    names = request.get('attachments', [])
    # Roots imported from Chat refer to the same question/files as their source
    # answer. Those files are already included in portable conversation archives.
    from_chat = bool(node['source_message_id'] and not names)
    if from_chat:
        # Polling a root only needs file references, not the entire Chat history.
        with service.store._connect() as db:
            question = db.execute('''SELECT id, attachment FROM chat_messages
                WHERE conversation_id=? AND id<? AND role='user' AND kind='chat' ORDER BY id DESC LIMIT 1''',
                (cid, node['source_message_id'])).fetchone()
            names = [row['path'] for row in db.execute(
                'SELECT path FROM chat_attachments WHERE message_id=? ORDER BY position', (question['id'],))] if question else []
            if question and question['attachment']:
                names.append(question['attachment'])
            source = db.execute('SELECT attachment FROM chat_messages WHERE conversation_id=? AND id=?',
                                (cid, node['source_message_id'])).fetchone()
            if source and source['attachment']:
                names.append(source['attachment'])
        folder = service.conversation(cid).folder
        names = list(dict.fromkeys(str((folder / name).resolve()) for name in names))
    with service.store._connect() as db:
        saved = {row['position']: dict(row) for row in db.execute('''
            SELECT n.position, n.filename, length(a.content) AS size FROM research_node_assets n
            JOIN research_assets a ON a.id=n.asset_id
            JOIN research_nodes r ON r.id=n.node_id
            WHERE r.conversation_id=? AND n.node_id=? ORDER BY n.position''', (cid, nid))}
    entries = []
    for position in sorted(set(range(len(names))) | saved.keys()):
        if position in saved:
            item = saved[position]
            entries.append(dict(position=position, name=item['filename'], size=item['size'], available=True, archived=True))
        else:
            path = Path(names[position])
            # Imported archives never use source-machine paths. If an archived
            # snapshot is missing, do not silently substitute a different file.
            try:
                available = (from_chat or not request.get('assets_in_database')) and path.is_file()
                size = path.stat().st_size if available else None
            except OSError:
                available, size = False, None
            entries.append(dict(position=position, name=path.name, size=size,
                                available=available, archived=False, local_path=path))
    return entries


def list_attachments(service, cid, nid):
    return [{key: value for key, value in item.items() if key not in ('local_path', 'archived')}
            | {'path': f"{nid}/{item['position']}", 'type': mimetypes.guess_type(item['name'])[0] or 'application/octet-stream'}
            for item in _entries(service, cid, nid)]


def read_attachment(service, cid, nid, position):
    item = next((item for item in _entries(service, cid, nid) if item['position'] == position), None)
    if not item or not item['available']:
        raise KeyError('Không còn file đính kèm này trong dữ liệu đã lưu.')
    if item['archived']:
        with service.store._connect() as db:
            row = db.execute('''SELECT a.content FROM research_node_assets n
                JOIN research_assets a ON a.id=n.asset_id JOIN research_nodes r ON r.id=n.node_id
                WHERE r.conversation_id=? AND n.node_id=? AND n.position=?''', (cid, nid, position)).fetchone()
        if row is None:
            raise KeyError('File đính kèm đã bị xóa.')
        return item['name'], row['content']
    try:
        return item['name'], item['local_path'].read_bytes()
    except FileNotFoundError as exc:
        raise KeyError('Không còn file đính kèm này trong dữ liệu đã lưu.') from exc
