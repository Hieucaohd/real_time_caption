"""Portable conversation snapshots. Import only data, never a database or executable code."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile

from caption import conversations
from caption.shared_access import capture_lock, ProcessLock
from .research_routes import BranchConfig, GraphLayoutPatch, ReadingMark
from .realtime_chat_routes import RealtimeConfig

FORMAT = 'real-time-caption-conversation'
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_MANIFEST = 32 * 1024 * 1024
MAX_FILES = 10000
TABLES = ('chat_messages', 'chat_attachments', 'research_nodes', 'research_node_assets',
          'research_graph_layouts', 'translation_lines', 'line_translations', 'realtime_translations',
          'realtime_chat_settings', 'realtime_chat_events')


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or name.startswith('/'):
        raise ValueError('Đường dẫn trong gói hội thoại không hợp lệ.')
    parts = name.split('/')
    for part in parts:
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or
                any(ord(c) < 32 or c in '<>"|?*' for c in part) or
                re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)):
            raise ValueError('Tên file trong gói hội thoại không hợp lệ.')
    return name


def json_object(text):
    def invalid(_):
        raise ValueError('Giá trị số trong gói hội thoại không hợp lệ.')
    value = json.loads(text, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError('Dữ liệu trong gói hội thoại phải là một object.')
    return value


def export_conversation(service, cid, destination):
    store = service.store
    # Capture/rename locks keep folder paths and transcript parts stable. The SQL
    # transaction snapshots all related tables together, including frozen assets.
    with service.lock, capture_lock(store.db_path), ProcessLock(store.db_path.parent / 'cache' / 'research-prepare.lock'), store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        conversation = dict(db.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone() or {})
        if not conversation:
            raise KeyError('Conversation not found')
        folder = store.get(cid).folder.resolve()
        rows = lambda sql: [dict(row) for row in db.execute(sql, (cid,))]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data = {'format': FORMAT, 'version': 1, 'conversation': conversation,
            'chat_messages': rows('SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY id'),
            'chat_attachments': rows('SELECT a.* FROM chat_attachments a JOIN chat_messages m ON m.id=a.message_id WHERE m.conversation_id=? ORDER BY a.message_id,a.position'),
            'research_nodes': rows('SELECT * FROM research_nodes WHERE conversation_id=? ORDER BY created_at,rowid'),
            'research_node_assets': rows('SELECT a.* FROM research_node_assets a JOIN research_nodes n ON n.id=a.node_id WHERE n.conversation_id=? ORDER BY a.node_id,a.position'),
            'research_graph_layouts': rows('SELECT l.* FROM research_graph_layouts l JOIN research_nodes n ON n.id=l.root_id WHERE n.conversation_id=?'),
            'translation_lines': rows('SELECT * FROM translation_lines WHERE conversation_id=? ORDER BY id'),
            'realtime_translations': rows('SELECT * FROM realtime_translations WHERE conversation_id=? ORDER BY id'),
            'realtime_chat_settings': rows('SELECT * FROM realtime_chat_settings WHERE conversation_id=?') if 'realtime_chat_settings' in tables else [],
            'realtime_chat_events': rows('SELECT * FROM realtime_chat_events WHERE conversation_id=? ORDER BY id') if 'realtime_chat_events' in tables else [],
            'line_translations': rows('SELECT t.* FROM line_translations t JOIN translation_lines l ON l.id=t.line_id WHERE l.conversation_id=? ORDER BY t.id'),
            'files': {}}
        total = 0
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            def write(name, source):
                nonlocal total
                safe_name(name)
                if name in data['files']:
                    return
                if len(data['files']) >= MAX_FILES - 1:
                    raise ValueError('Cuộc hội thoại có quá nhiều file để export.')
                digest, size = hashlib.sha256(), 0
                with archive.open(name, 'w') as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        size += len(chunk); total += len(chunk)
                        if total > MAX_EXPANDED:
                            raise ValueError('Dữ liệu hội thoại vượt giới hạn 2 GB.')
                        digest.update(chunk); output.write(chunk)
                data['files'][name] = {'size': size, 'sha256': digest.hexdigest()}

            for path in sorted(folder.rglob('*')):
                if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                    raise ValueError('Không export thư mục có liên kết tới dữ liệu bên ngoài.')
                if path.is_file():
                    before = path.stat()
                    with path.open('rb') as stream:
                        write('files/' + path.relative_to(folder).as_posix(), stream)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('File đang thay đổi. Hãy đợi thao tác ghi hoàn tất rồi export lại.')

            external = {}
            def attachment(value):
                if not value:
                    return value
                path = Path(value)
                path = (path if path.is_absolute() else folder / path).resolve()
                if path.is_relative_to(folder):
                    relative = path.relative_to(folder).as_posix()
                    if 'files/' + relative not in data['files']:
                        raise ValueError('Thiếu file đính kèm: ' + path.name)
                    return relative
                if path not in external:
                    relative = 'attachments/export-' + uuid.uuid4().hex + '/' + path.name
                    with path.open('rb') as stream:
                        write('files/' + relative, stream)
                    external[path] = relative
                return external[path]
            for message in data['chat_messages']:
                message['attachment'] = attachment(message['attachment'])
            for item in data['chat_attachments']:
                item['path'] = attachment(item['path'])
            for event in data['realtime_chat_events']:
                event['files'] = json.dumps([attachment(path) for path in json.loads(event['files'])])
            from io import BytesIO
            for digest in {item['asset_id'] for item in data['research_node_assets']}:
                row = db.execute('SELECT content FROM research_assets WHERE id=?', (digest,)).fetchone()
                if row is None:
                    raise ValueError('Thiếu file đã lưu của node nghiên cứu.')
                write('assets/' + digest, BytesIO(row['content']))
            # Include legacy frozen files too, before discarding machine-specific paths.
            links = defaultdict(list)
            for item in data['research_node_assets']:
                links[item['node_id']].append(item)
            for node in data['research_nodes']:
                request = json_object(node['request'])
                if not request.get('assets_in_database'):
                    for position, value in enumerate(request.get('attachments', [])):
                        content = Path(value).read_bytes()
                        digest = hashlib.sha256(content).hexdigest()
                        write('assets/' + digest, BytesIO(content))
                        item = dict(node_id=node['id'], position=position, asset_id=digest, filename=Path(value).name)
                        data['research_node_assets'].append(item); links[node['id']].append(item)
                if len(links[node['id']]) != len(request.get('attachments', [])):
                    raise ValueError('File đính kèm của node chưa đầy đủ; chưa thể export.')
                request['attachments'] = [item['filename'] for item in sorted(links[node['id']], key=lambda item: item['position'])]
                request['assets_in_database'] = True
                node['request'] = json.dumps(request, ensure_ascii=False)
            manifest = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(manifest) > MAX_MANIFEST:
                raise ValueError('Metadata hội thoại vượt giới hạn 32 MB.')
            if total + len(manifest) > MAX_EXPANDED:
                raise ValueError('Dữ liệu hội thoại vượt giới hạn 2 GB.')
            archive.writestr('manifest.json', manifest)
        if Path(destination).stat().st_size > MAX_ARCHIVE:
            raise ValueError('File export vượt giới hạn 512 MB.')


def _unpack(archive, stage):
    infos, seen, total = {}, set(), 0
    if len(archive.infolist()) > MAX_FILES:
        raise ValueError('Gói hội thoại có quá nhiều file.')
    for info in archive.infolist():
        name = safe_name(info.filename.rstrip('/') if info.is_dir() else info.filename)
        if name.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError('Gói hội thoại chứa file trùng, liên kết hoặc file mã hóa.')
        seen.add(name.casefold())
        total += info.file_size
        if total > MAX_EXPANDED or (name == 'manifest.json' and info.file_size > MAX_MANIFEST):
            raise ValueError('Gói hội thoại vượt giới hạn kích thước.')
        if info.is_dir():
            continue
        if name != 'manifest.json' and not name.startswith(('files/', 'assets/')):
            raise ValueError('Gói hội thoại chứa thành phần không được hỗ trợ.')
        infos[name] = info
    data = json_object(archive.read(infos['manifest.json']))
    if data.get('format') != FORMAT or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Đây không phải gói hội thoại được hỗ trợ. Hãy dùng bản app mới nhất.')
    # Older v1 exports predate the independent realtime translation history.
    data.setdefault('realtime_translations', [])
    data.setdefault('realtime_chat_settings', [])
    data.setdefault('realtime_chat_events', [])
    if set(data) != {'format', 'version', 'conversation', 'files', *TABLES}:
        raise ValueError('Cấu trúc gói hội thoại không hợp lệ.')
    if not isinstance(data['files'], dict) or set(data['files']) != set(infos) - {'manifest.json'}:
        raise ValueError('Danh sách file của gói hội thoại không đầy đủ.')
    for name, expected in data['files'].items():
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with archive.open(infos[name]) as source, target.open('xb') as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                size += len(chunk)
                if size > infos[name].file_size:
                    raise ValueError('Kích thước file trong gói không hợp lệ.')
                digest.update(chunk); output.write(chunk)
        if expected != {'size': size, 'sha256': digest.hexdigest()}:
            raise ValueError('Gói hội thoại bị hỏng: nội dung file không khớp mã kiểm tra.')
        if name.startswith('assets/') and name != 'assets/' + digest.hexdigest():
            raise ValueError('File đính kèm nghiên cứu không hợp lệ.')
    (stage / 'files').mkdir(exist_ok=True)
    return data


def _insert(db, table, row):
    schema = {column['name']: column for column in db.execute(f'PRAGMA table_info({table})')}
    if not isinstance(row, dict) or not row or not set(row) <= schema.keys():
        raise ValueError('Gói có trường dữ liệu chưa được hỗ trợ. Hãy cập nhật app.')
    for key, value in row.items():
        if value is not None and ((schema[key]['type'] == 'TEXT' and not isinstance(value, str)) or
                                  (schema[key]['type'] == 'INTEGER' and type(value) is not int)):
            raise ValueError('Kiểu dữ liệu trong gói hội thoại không hợp lệ.')
    columns = ','.join('"' + key + '"' for key in row)
    return db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})', tuple(row.values())).lastrowid


def _restore(store, data, stage, final, publication):
    base = conversations.CONVERSATIONS_DIR.resolve()
    if (stage.resolve().parent != base or not stage.name.startswith('.import-') or
            final.resolve().parent != base or not final.name.startswith('import-') or final.exists()):
        raise ValueError('Thư mục import không hợp lệ hoặc đã tồn tại.')
    cid = uuid.uuid4().hex
    original_cid = data['conversation']['id']
    for table in TABLES:
        if not isinstance(data[table], list) or not all(isinstance(row, dict) for row in data[table]):
            raise ValueError('Bảng dữ liệu trong gói không hợp lệ.')
        for row in data[table]:
            if 'conversation_id' in row and row['conversation_id'] != original_cid:
                raise ValueError('Gói chứa dữ liệu thuộc cuộc hội thoại khác.')
    conv = {**data['conversation'], 'id': cid, 'folder_name': final.name,
            'status': 'paused', 'legacy_path': None, 'updated_at': conversations._now()}
    if not isinstance(conv['title'], str) or not conv['title'].strip():
        raise ValueError('Tên cuộc hội thoại không hợp lệ.')
    # Check required metadata before publishing anything; no fallible reads after commit.
    result = conversations.Conversation(**{key: conv[key] for key in conversations.Conversation.__dataclass_fields__})
    def file_ref(value):
        if value is not None:
            safe_name(value)
            if 'files/' + value not in data['files']:
                raise ValueError('Không tìm thấy file đính kèm trong gói.')
        return value
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _insert(db, 'conversations', conv)
        message_ids, node_ids, line_ids = {}, {}, {}
        for message in data['chat_messages']:
            old_id = message['id']
            if type(old_id) is not int or old_id in message_ids:
                raise ValueError('ID tin nhắn bị trùng hoặc không hợp lệ.')
            row = {**message, 'conversation_id': cid, 'attachment': file_ref(message['attachment'])}
            del row['id']
            message_ids[old_id] = _insert(db, 'chat_messages', row)
        for item in data['chat_attachments']:
            _insert(db, 'chat_attachments', {**item, 'message_id': message_ids[item['message_id']], 'path': file_ref(item['path'])})
        by_id, descendants, queue = {}, defaultdict(list), deque()
        for node in data['research_nodes']:
            nid = node['id']
            if not isinstance(nid, str) or not nid or nid in by_id:
                raise ValueError('ID node bị trùng hoặc không hợp lệ.')
            by_id[nid] = node; node_ids[nid] = uuid.uuid4().hex
            descendants[node['parent_id']].append(nid)
            if node['parent_id'] is None:
                queue.append(nid)
        ordered = []
        while queue:
            nid = queue.popleft(); node = by_id[nid]
            root = node['root_id']
            if root != (nid if node['parent_id'] is None else by_id[node['parent_id']]['root_id']):
                raise ValueError('Quan hệ node gốc không hợp lệ.')
            ordered.append(node); queue.extend(descendants[nid])
        if len(ordered) != len(by_id):
            raise ValueError('Cây nghiên cứu có chu trình hoặc thiếu node cha.')
        assets = defaultdict(list)
        for item in data['research_node_assets']:
            node_ids[item['node_id']]
            if '/' in safe_name(item['filename']):
                raise ValueError('Tên file của node không hợp lệ.')
            name = 'assets/' + item['asset_id']
            if name not in data['files']:
                raise ValueError('Thiếu file nghiên cứu trong gói.')
            assets[item['node_id']].append(item)
        for node in ordered:
            config = BranchConfig.model_validate(json_object(node['config'])).model_dump(exclude_none=True)
            config['selected_tab'] = ''  # Chrome tab IDs belong to the source device.
            request = json_object(node['request'])
            links = sorted(assets[node['id']], key=lambda item: item['position'])
            if [item['position'] for item in links] != list(range(len(links))) or len(links) != len(request.get('attachments', [])):
                raise ValueError('File đính kèm của node không đầy đủ.')
            if not isinstance(request.get('prompt', ''), str):
                raise ValueError('Prompt của node không hợp lệ.')
            request.update(attachments=[item['filename'] for item in links], assets_in_database=True,
                           ancestor_ids=[node_ids[nid] for nid in request.get('ancestor_ids', [])])
            for key in ('reading_mark', 'excerpt_mark'):
                if node.get(key):
                    ReadingMark.model_validate(json_object(node[key]))
            row = {**node, 'id': node_ids[node['id']], 'conversation_id': cid,
                'parent_id': node_ids[node['parent_id']] if node['parent_id'] else None,
                'root_id': node_ids[node['root_id']],
                'source_message_id': message_ids[node['source_message_id']] if node['source_message_id'] is not None else None,
                'owner_pid': 0, 'config': json.dumps(config, ensure_ascii=False), 'request': json.dumps(request, ensure_ascii=False)}
            if row['status'] in ('queued', 'working'):
                row.update(status='interrupted', detail='Đã nhập từ thiết bị khác. Chọn tab ChatGPT rồi thử lại khi cần.')
            _insert(db, 'research_nodes', row)
        for name in data['files']:
            if name.startswith('assets/'):
                db.execute('INSERT OR IGNORE INTO research_assets(id,content) VALUES (?,?)',
                           (name.split('/')[1], (stage / name).read_bytes()))
        for item in data['research_node_assets']:
            _insert(db, 'research_node_assets', {**item, 'node_id': node_ids[item['node_id']]})
        for item in data['research_graph_layouts']:
            if by_id[item['root_id']]['parent_id'] is not None:
                raise ValueError('Bố cục không thuộc node gốc.')
            state = GraphLayoutPatch.model_validate(json_object(item['state'])).model_dump(exclude_none=True)
            refs = set(state.get('positions', {})) | set(state.get('collapsed', []))
            if any(by_id[nid]['root_id'] != item['root_id'] for nid in refs):
                raise ValueError('Bố cục chứa node thuộc cây khác.')
            if 'positions' in state:
                state['positions'] = {node_ids[nid]: point for nid, point in state['positions'].items()}
            if 'collapsed' in state:
                state['collapsed'] = [node_ids[nid] for nid in state['collapsed']]
            _insert(db, 'research_graph_layouts', {**item, 'root_id': node_ids[item['root_id']], 'state': json.dumps(state)})
        for item in data['translation_lines']:
            old_id = item['id']
            if type(old_id) is not int or old_id in line_ids:
                raise ValueError('ID dòng transcript bị trùng hoặc không hợp lệ.')
            row = {**item, 'conversation_id': cid}; del row['id']
            line_ids[old_id] = _insert(db, 'translation_lines', row)
        for item in data['line_translations']:
            row = {**item, 'line_id': line_ids[item['line_id']], 'owner_pid': 0}; del row['id']
            if row['status'] in ('queued', 'working'):
                row.update(status='error', error='Tác vụ chưa hoàn tất trên thiết bị nguồn. Dịch lại khi cần.')
            _insert(db, 'line_translations', row)
        for item in data['realtime_translations']:
            row = {**item, 'conversation_id': cid}; del row['id']
            _insert(db, 'realtime_translations', row)
        for item in data['realtime_chat_settings']:
            config = RealtimeConfig.model_validate(json_object(item['config'])).model_dump()
            config.update(selected_tab='', selected_tab_title='', summary_tab='', summary_tab_title='', new_chat=False)
            _insert(db, 'realtime_chat_settings', {**item, 'conversation_id': cid,
                'config': json.dumps(config, ensure_ascii=False), 'actions_cursor': '{}', 'summary_cursor': '{}'})
        for item in data['realtime_chat_events']:
            files = json.loads(item['files'])
            if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
                raise ValueError('File Chat realtime không hợp lệ.')
            row = {**item, 'conversation_id': cid, 'files': json.dumps([file_ref(path) for path in files])}
            del row['id']
            json_object(row['response_source'])
            if row['kind'] not in ('chat', 'actions', 'summary'):
                raise ValueError('Loại dữ liệu Chat realtime không hợp lệ.')
            if row['status'] in ('queued', 'working', 'received'):
                row.update(status='interrupted', detail='Tác vụ chưa hoàn tất trên thiết bị nguồn.')
            if row['kind'] == 'actions' and row['status'] == 'done':
                from .realtime_chat import parse_actions
                actions = json.loads(row['answer'])
                # Revalidate and assign local action IDs instead of trusting archive data.
                row['answer'] = json.dumps(parse_actions(json.dumps({'actions': actions})), ensure_ascii=False)
            _insert(db, 'realtime_chat_events', row)
        manifest = {key: conv[key] for key in ('id', 'title', 'source', 'created_at', 'updated_at', 'last_started_at', 'status')}
        (stage / 'files' / 'conversation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        history = [f"# Chat history — {conv['title']}", '']
        for message in data['chat_messages']:
            if message['kind'] != 'vocab':
                label = 'You' if message['role'] == 'user' else 'ChatGPT'
                history.extend([f"## {label} · {message['created_at']}", '', message['content'].strip(), ''])
                files = [PurePosixPath(item['path']).name for item in data['chat_attachments'] if item['message_id'] == message['id']]
                if files:
                    history.extend(['Attached files: ' + ', '.join(files), ''])
        (stage / 'files' / 'chat_history.md').write_text('\n'.join(history), encoding='utf-8')
        (stage / 'files' / 'screenshots').mkdir(exist_ok=True)
        (stage / 'files').rename(final)
        publication['moved'] = True
    return result


def import_conversation(service, source):
    store = service.store
    base = conversations.CONVERSATIONS_DIR.resolve()
    base.mkdir(parents=True, exist_ok=True)
    final = base / ('import-' + uuid.uuid4().hex)
    publication = {'moved': False}
    # Validate/extract only into our own staging directory; make it visible in one
    # SQL transaction and rename. A rejected import leaves no conversation behind.
    with tempfile.TemporaryDirectory(prefix='.import-', dir=base) as temporary:
        try:
            with zipfile.ZipFile(source) as archive:
                data = _unpack(archive, Path(temporary))
            with service.lock:
                if not hasattr(service, 'realtime_chat'):
                    from .realtime_chat_store import RealtimeChatStore
                    RealtimeChatStore(store)  # Native-only databases need the new tables too.
                return _restore(store, data, Path(temporary), final, publication)
        except Exception as exc:
            if publication['moved'] and final.exists() and final.resolve().parent == base and final.name.startswith('import-'):
                shutil.rmtree(final)
            if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, sqlite3.IntegrityError,
                                zipfile.BadZipFile, NotImplementedError, EOFError, FileExistsError, IsADirectoryError)):
                raise ValueError('Không import được gói hội thoại: ' + str(exc)) from exc
            raise
