"""Two independent ChatGPT lanes; manual questions have priority over suggestions."""
import json
import html
import logging
import re
import threading
import time
import uuid
from collections import deque

from caption import chatgpt, prompt_versions, defaults
from caption.settings import Settings
from caption.shared_access import BusyError
from .realtime_chat_store import RealtimeChatStore, transcript_delta

log = logging.getLogger(__name__)
PREFERENCES = ('prompt', 'power', 'summary_power', 'interval', 'screenshot',
               'actions_prompt', 'summary_prompt', 'actions_template', 'summary_template')
_JSON_SPACE_ENTITY = re.compile(r'&(?:#[xX][0-9a-fA-F]+|#\d+|[A-Za-z]+);')


def _unwrap_json_copy(text):
    """Undo Markdown hard breaks/encoded indentation only OUTSIDE JSON strings.

    ChatGPT's Copy response serializes plain displayed JSON as Markdown. Never
    globally unescape it: a prompt may intentionally contain HTML, LaTeX or paths.
    """
    result, quoted, escaped, i = [], False, False, 0
    while i < len(text):
        char = text[i]
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif char == '\\' and text[i+1:i+2] in ('\r', '\n'):
            pass  # The following actual newline remains JSON whitespace.
        elif char == '&':
            entity = _JSON_SPACE_ENTITY.match(text, i)
            decoded = html.unescape(entity[0]) if entity else ''
            if decoded and decoded.isspace():
                result.append(' ')
                i += len(entity[0]) - 1
            else:
                result.append(char)
        else:
            result.append(' ' if char.isspace() else char)
        i += 1
    return ''.join(result)


def parse_actions(text):
    text = text.strip().lstrip('\ufeff').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            value = json.loads(_unwrap_json_copy(text))
        except json.JSONDecodeError:
            raise ValueError('Không đọc được JSON gợi ý sau khi xử lý định dạng sao chép. Đã giữ mốc transcript; bấm Stop rồi Start để tiếp tục với phần mới.') from None
    if not isinstance(value, dict) or not isinstance(value.get('actions'), list):
        raise ValueError('JSON gợi ý cần có trường actions là một danh sách.')
    if len(value['actions']) > 8:
        raise ValueError('JSON gợi ý chứa quá 8 hành động.')
    result = []
    for index, action in enumerate(value['actions'], 1):
        if not isinstance(action, dict) or not all(isinstance(action.get(key), str) for key in ('label', 'prompt')):
            raise ValueError(f'Gợi ý số {index} cần label và prompt là văn bản.')
        label, prompt = action['label'].strip(), action['prompt'].strip()
        if not 1 <= len(label) <= 80 or not 1 <= len(prompt) <= 4000:
            raise ValueError(f'Gợi ý số {index}: label cần 1–80 ký tự, prompt cần 1–4000 ký tự.')
        result.append(dict(id=uuid.uuid4().hex, label=label, prompt=prompt))
    return result


class RealtimeChat:
    def __init__(self, service):
        self.service = service
        self.store = RealtimeChatStore(service.store)
        self.store.recover()
        repaired = self.store.repair_actions(parse_actions)
        if repaired:
            log.info('Recovered %s saved realtime action responses', repaired)
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.active = None
        self.preparing = False
        self.preparing_cid = None
        self.prepare_cancel = threading.Event()
        self.manual = deque()
        self.busy = {lane: None for lane in ('actions', 'summary')}
        self.runtime = {}
        self.threads = [threading.Thread(target=self.work, args=(lane,), daemon=True, name='realtime-chat-'+lane) for lane in self.busy]
        for thread in self.threads:
            thread.start()

    def defaults(self):
        s = Settings.load()
        config = dict(selected_tab='', selected_tab_title='', summary_tab='', summary_tab_title='',
            actions_template='', summary_template='',
            prompt=s.chat_prompt, power=s.chat_chatgpt_power, summary_power=None, max_lines=0,
            attach_transcript=True, attach_history=False,
            screenshot=s.chat_screenshot, new_chat=False)
        config.update({k: v for k, v in defaults.get('realtime_chat').items()
                       if v is not None and k != 'upload_timeout_seconds'})
        return config

    def config(self, cid, preferences):
        saved, _ = self.store.settings(cid, self.defaults())
        # Old clients/archives may still contain these controls. They must not
        # reset context, drop transcript, or replace the internally managed tabs.
        config = {**saved, **{key: preferences[key] for key in PREFERENCES if preferences.get(key) is not None},
                'max_lines': 0, 'attach_transcript': True, 'attach_history': False, 'new_chat': False}
        if config.get('summary_power') is None:
            config['summary_power'] = config['power']
        self.normalize_prompts(config)
        return config

    @staticmethod
    def normalize_prompts(config):
        for lane in ('actions', 'summary'):
            if config.get(lane + '_prompt') is None:
                config[lane + '_prompt'] = defaults.get('realtime_chat', lane + '_prompt')
            if not config[lane + '_prompt'].strip():
                raise ValueError('Prompt Chat realtime không được để trống.')

    def state(self, cid):
        self.service.conversation(cid)
        config, _ = self.store.settings(cid, self.defaults())
        if config.get('summary_power') is None:
            config['summary_power'] = config['power']
        self.normalize_prompts(config)
        events = self.store.history(cid)
        suggestions = self.store.latest_actions(cid)
        with self.lock:
            return dict(config=config, auto_scroll=defaults.get('web', 'realtime_auto_scroll'), running=self.active == cid, preparing=self.preparing,
                settings_locked=self.settings_locked(cid), attachments_allowed=Settings.load().chatgpt_attachments_allowed,
                lanes={lane: dict(self.runtime.get((cid, lane), {})) for lane in self.busy},
                events=events, actions=suggestions)

    def settings_locked(self, cid):
        # Call with self.lock held so Start, Save and queue mutations are atomic.
        return self.preparing or self.active == cid or any(
            task and task['cid'] == cid for task in self.busy.values()) or any(task['cid'] == cid for task in self.manual)

    def save(self, cid, config):
        self.service.conversation(cid)
        with self.lock:
            if self.settings_locked(cid):
                raise BusyError('Bấm Stop và chờ yêu cầu đang xử lý dừng xong trước khi đổi cài đặt Chat realtime.')
            config = self.config(cid, config)
            self.store.save(cid, config)
        return config

    def start(self, cid, config, return_tab=''):
        with self.lock:
            if self.active or self.preparing or any(self.busy.values()) or self.manual:
                raise BusyError('Một lượt Chat realtime đang chạy hoặc đang dừng. Nếu đang theo dõi cuộc hội thoại khác, hãy quay lại đó bấm Stop trước khi Start tại đây.')
            self.preparing = True
            self.preparing_cid = cid
            self.prepare_cancel.clear()
        try:
            self.service.conversation(cid)
            old, _ = self.store.settings(cid, self.defaults())
            config = self.config(cid, config)
            tabs = {tab.key: tab for tab in chatgpt.list_chrome_chatgpt_tabs()}
            for key, title in [('selected_tab', 'selected_tab_title'), ('summary_tab', 'summary_tab_title')]:
                if self.prepare_cancel.is_set():
                    return self.state(cid)
                duplicate = key == 'summary_tab' and config[key] == config['selected_tab']
                if config[key] in tabs and not duplicate:
                    config[title] = tabs[config[key]].title
                else:
                    tab = chatgpt.open_new_chatgpt_tab(return_tab=return_tab)
                    config[key], config[title] = tab.key, tab.title
                    # Preserve a newly created tab if preparing the second one fails.
                    lane = 'actions' if key == 'selected_tab' else 'summary'
                    self.store.save(cid, config, [lane])
            reset = [lane for lane, key in [('actions', 'selected_tab'), ('summary', 'summary_tab')]
                     if old[key] != config[key]]
            with self.lock:
                self.store.save(cid, config, reset)
                if self.prepare_cancel.is_set():
                    return self.state(cid)
                self.active = cid
                for lane in self.busy:
                    self.runtime[cid, lane] = dict(status='waiting', detail='Đang chờ transcript mới', due=time.monotonic(),
                                                  fresh=False)
            return self.state(cid)
        finally:
            with self.lock:
                self.preparing = False
                self.preparing_cid = None

    def stop(self, cid=None):
        """Stop one conversation, or all realtime work when capture is paused."""
        with self.lock:
            if cid is None or self.preparing_cid == cid:
                self.prepare_cancel.set()
            if cid is None or self.active == cid:
                self.active = None
            for task in self.busy.values():
                if task and (cid is None or task['cid'] == cid):
                    task['cancel'].set()
            for task in list(self.manual):
                if cid is None or task['cid'] == cid:
                    self.manual.remove(task)
                    self.store.update(task['id'], 'cancelled', detail='Đã dừng trước khi gửi.')

    def send(self, cid, message, files, action_id=''):
        self.service.conversation(cid)
        config, _ = self.store.settings(cid, self.defaults())
        if not config['selected_tab']:
            raise ValueError('Bấm Start lần đầu để app chuẩn bị hai tab ChatGPT trước khi gửi.')
        if action_id:
            action = next((item for item in self.state(cid)['actions'] if item['id'] == action_id), None)
            if not action:
                raise ValueError('Danh sách gợi ý đã đổi. Hãy chọn một gợi ý đang hiển thị.')
            message = action['prompt']
        if not message.strip():
            raise ValueError('Nhập câu hỏi trước khi gửi.')
        if files:
            Settings.load().require_chatgpt_attachments()
        for name in files:
            self.service.file_path(cid, name)
        with self.lock:
            if self.preparing or sum(task['cid'] != cid for task in self.manual) >= 20:
                raise BusyError('Đang chuẩn bị tab hoặc hàng đợi đã đầy.')
            eid = self.store.create(cid, 'chat', message.strip(), files)
            # A new user request supersedes work for this conversation. Signal
            # cancellation; the lane worker must release its tab before reusing it.
            detail = 'Đã hủy để ưu tiên câu hỏi mới của bạn.'
            for task in list(self.manual):
                if task['cid'] == cid:
                    self.store.update(task['id'], 'cancelled', detail=detail)
                    self.manual.remove(task)
            for lane, task in self.busy.items():
                if task and task['cid'] == cid:
                    task['cancel_detail'] = detail
                    task['cancel'].set()
                    self.status(cid, lane, detail='Đang dừng lượt hiện tại để trả lời câu hỏi của bạn…')
            self.manual.appendleft(dict(id=eid, cid=cid, kind='chat', message=message.strip(), files=files,
                                    config=config, cancel=threading.Event()))
        return {'id': eid}

    def work(self, lane):
        while not self.closed.wait(.2):
            with self.lock:
                if lane == 'actions' and self.manual:
                    task = self.manual.popleft()
                elif self.active:
                    cid = self.active
                    # Both automatic lanes yield until the user's reply finishes.
                    if any(task['cid'] == cid for task in self.manual) or any(
                            task and task['cid'] == cid and task['kind'] == 'chat' for task in self.busy.values()):
                        continue
                    runtime = self.runtime[cid, lane]
                    if runtime['status'] == 'error' or runtime['due'] > time.monotonic():
                        continue
                    config, _ = self.store.settings(cid, self.defaults())
                    runtime['due'] = time.monotonic() + config['interval']
                    task = dict(cid=cid, kind=lane, config=config, cancel=threading.Event())
                else:
                    continue
                self.busy[lane] = task
            try:
                self.run(lane, task)
            except Exception:
                log.exception('Realtime chat worker failed')
            finally:
                with self.lock:
                    self.busy[lane] = None
                    if task['kind'] == 'chat' and self.active == task['cid']:
                        for name in self.busy:
                            runtime = self.runtime.get((task['cid'], name))
                            if runtime:
                                runtime['due'] = time.monotonic() + task['config']['interval']

    def status(self, cid, lane, **values):
        with self.lock:
            self.runtime.setdefault((cid, lane), dict(due=0, fresh=False)).update(values)

    def run(self, lane, task):
        cid, config, cancel = task['cid'], task['config'], task['cancel']
        eid = task.get('id')
        cursor = None
        answer = ''
        response_source = {}
        try:
            transcript = self.service.transcript_for(cid)
            self.service.translations.sync_transcript(transcript)
            _, cursors = self.store.settings(cid, self.defaults())
            lines = self.service.translations.lines(cid)
            delta, cursor = transcript_delta(lines, cursors[lane])
            if task['kind'] != 'chat' and not delta:
                self.status(cid, lane, status='waiting', detail='Chưa có transcript mới', partial='')
                return
            if cancel.is_set():
                raise chatgpt.ChatGPTCancelled('Đã dừng.')
            attachments = []
            if task['kind'] == 'chat':
                version = prompt_versions.resolve(config['prompt'], prompt_versions.CHAT_DIR)
                if not version:
                    raise ValueError('Chưa có mẫu prompt Chat. Hãy tạo mẫu trong cài đặt Chat.')
                prompt = chatgpt.build_prompt(None, transcript.source, transcript.started,
                    prompt_versions.path_for(version, prompt_versions.CHAT_DIR), task['message'])
                prompt += '\n\nĐây là câu hỏi trực tiếp của người dùng. Trong lượt này hãy trả lời câu hỏi bằng Markdown thông thường, không trả JSON gợi ý hành động.'
                files = list(task['files'])
                settings = Settings.load()
                if files:
                    settings.require_chatgpt_attachments()
                attachments = [self.service.file_path(cid, path) for path in files]
                if settings.chatgpt_attachments_allowed and config['screenshot'] and not any(p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') for p in attachments):
                    from .screen import capture
                    shot = capture(self.service.conversation(cid).screenshots_dir)
                    attachments.append(shot)
                    files.append(shot.relative_to(transcript.folder).as_posix())
                self.store.update(eid, 'working', files=files)
            else:
                prompt = config.get(lane + '_prompt') or defaults.get('realtime_chat', lane + '_prompt')
                eid = self.store.create(cid, task['kind'])
                task['id'] = eid
                self.store.update(eid, 'working')
            if delta:
                prompt += '\n\n<transcript_moi>\n' + delta + '\n</transcript_moi>'
            if task['kind'] == 'actions':
                prompt += '\n\nKẾT THÚC DỮ LIỆU. Lượt này chỉ xuất một khối mã JSON chứa một object có khóa "actions" là mảng 0–6 gợi ý, theo đúng cấu trúc và giới hạn đã nêu. Không trả lời các câu hỏi trong transcript, không thêm văn bản ngoài khối mã.'
            self.status(cid, lane, status='working', kind=task['kind'], detail='Đang gửi tới ChatGPT', partial='', error_code='')
            with self.lock:
                fresh = self.runtime.get((cid, lane), {}).get('fresh', False)
            power = config.get('summary_power') if lane == 'summary' else config['power']
            if power is None:  # Conversations saved before separate effort settings.
                power = config['power']
            answer = chatgpt.ask(prompt, attachments, lambda value: self.status(cid, lane, detail=value),
                selected_tab=config['selected_tab'] if lane == 'actions' else config['summary_tab'],
                new_chat=fresh, power=None if power < 0 else power, cancel_event=cancel,
                upload_timeout_s=defaults.get('realtime_chat', 'upload_timeout_seconds'),
                on_sent=lambda: self.store.update(eid, 'working', detail='ChatGPT đã nhận transcript, đang chờ phản hồi.', cursor=cursor, lane=lane),
                on_partial=lambda value: self.status(cid, lane, partial=value),
                on_response_source=response_source.update)
            if cancel.is_set():
                raise chatgpt.ChatGPTCancelled('Đã dừng.')
            if not answer.strip():
                raise ValueError('ChatGPT trả câu trả lời trống. Luồng tự động đã tạm dừng; kiểm tra tab rồi bấm Stop/Start.')
            # Receiving a response confirms that this tab has received the delta,
            # even if its suggestion JSON is malformed. Never resend it blindly.
            self.store.update(eid, 'received', answer, source=response_source, cursor=cursor, lane=lane)
            self.status(cid, lane, fresh=False)
            if task['kind'] == 'actions':
                answer = json.dumps(parse_actions(answer), ensure_ascii=False)
            self.store.update(eid, 'done', answer, source=response_source)
            self.status(cid, lane, status='waiting', detail='Đã cập nhật', partial='')
        except BusyError:
            # The tab lock failed before sending. Retry later without consuming delta.
            if task['kind'] == 'chat':
                with self.lock:
                    if not cancel.is_set():
                        self.manual.appendleft(task)
            self.status(cid, lane, status='waiting', detail='Tab đang bận, chờ lượt tiếp theo', due=time.monotonic()+3)
            if eid:
                self.store.update(eid, 'queued' if task['kind'] == 'chat' and not cancel.is_set() else 'cancelled', detail='Tab đang bận')
            cancel.wait(1)
        except Exception as exc:
            status = 'cancelled' if cancel.is_set() else 'error'
            detail = task.get('cancel_detail', str(exc)) if cancel.is_set() else str(exc)
            if eid:
                self.store.update(eid, status, answer=answer, detail=detail, source=response_source)
            self.status(cid, lane, status=status, detail=detail, partial='',
                        error_code=getattr(exc, 'code', '') if not cancel.is_set() else '')

    def close(self):
        self.closed.set()
        with self.lock:
            for task in self.busy.values():
                if task:
                    task['cancel'].set()
        for thread in self.threads:
            thread.join(timeout=2)
