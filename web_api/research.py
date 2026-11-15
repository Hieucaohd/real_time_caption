"""Durable branch queue using Chat prompts and the selected Chrome conversation."""
from __future__ import annotations

import logging
import shutil
import threading
import uuid

from caption import chatgpt, paths, prompt_versions
from caption.research_store import ResearchStore
from caption.settings import Settings
from caption.shared_access import BusyError, ProcessLock

log = logging.getLogger(__name__)


class Research:
    def __init__(self, service):
        self.service = service
        self.store = ResearchStore(service.store)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.work, daemon=True, name='branch-research')
        self.thread.start()

    def children(self, cid, pid, payload):
        with self.service.lock, ProcessLock(paths.CACHE_DIR / 'research-prepare.lock'):
            parent = self.store.get(cid, pid)
            if parent['status'] != 'done':
                raise ValueError('Wait for the parent answer before creating its children.')
            settings = Settings.load()
            config = dict(selected_tab=settings.chatgpt_selected_tab,
                          power=settings.chat_chatgpt_power, attach_transcript=False,
                          max_lines=settings.chat_max_lines, screenshot=settings.chat_screenshot,
                          attach_history=settings.chat_attach_history, prompt=settings.chat_prompt,
                          new_chat=False, ancestor_depth=0)
            config.update(payload.get('config', {}))
            if not config['selected_tab']:
                raise ValueError('Chọn tab ChatGPT cho nghiên cứu trước khi xếp hàng.')
            version = prompt_versions.resolve(config['prompt'], prompt_versions.CHAT_DIR)
            if not version:
                raise ValueError('No prompt version found.')
            config['prompt'] = version
            depth = config['ancestor_depth']
            if type(depth) is not int or not -1 <= depth <= 10000:
                raise ValueError('Số cấp ngữ cảnh tổ tiên không hợp lệ.')
            ancestors = self.store.ancestry(cid, pid) if depth else []
            if depth > 0:
                ancestors = ancestors[-depth:]
            if any(node['status'] != 'done' for node in ancestors):
                raise ValueError('Chờ các câu trả lời trong ngữ cảnh tổ tiên hoàn tất trước khi đính kèm.')
            transcript = self.service.transcript_for(cid)
            # Freeze files when queued, so later renames/edits do not change a branch or its retry.
            folder = paths.CACHE_DIR / 'research' / uuid.uuid4().hex
            folder.mkdir(parents=True, exist_ok=True)
            attachments = []
            for index, name in enumerate(payload.get('files', [])):
                original = self.service.file_path(cid, name)
                target = folder / f'{index}_{original.name}'
                shutil.copy2(original, target)
                attachments.append(str(target))
            if config['screenshot'] and not any(
                self.service.file_path(cid, name).suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')
                for name in payload.get('files', [])
            ):
                from .screen import capture
                attachments.append(str(capture(folder)))
            upload = None
            if config['attach_transcript']:
                if not transcript.has_text:
                    raise ValueError('Cuộc hội thoại chưa có transcript. Tắt đính kèm transcript hoặc ghi nội dung trước.')
                original = transcript.snapshot(config['max_lines'])
                target = folder / original.name
                shutil.copy2(original, target)
                attachments.append(str(target))
                upload = target
            if config['attach_history']:
                history = self.service.store.chat_context_snapshot(cid)
                if history:
                    target = folder / history.name
                    shutil.copy2(history, target)
                    attachments.append(str(target))
            if ancestors:
                context_file = folder / 'research-ancestors.md'
                sections = ['# Ngữ cảnh nghiên cứu theo nhánh',
                            'Các cặp câu hỏi và câu trả lời được chọn, theo thứ tự từ cũ nhất đến node cha.']
                for index, ancestor in enumerate(ancestors, 1):
                    sections.append(f"## {index}. {ancestor['title']}\n\n### Câu hỏi\n\n{ancestor['question']}\n\n### Câu trả lời\n\n{ancestor['answer']}")
                context_file.write_text('\n\n---\n\n'.join(sections) + '\n', encoding='utf-8')
                attachments.append(str(context_file))
            prepared = []
            for question in payload['questions']:
                prompt = chatgpt.build_prompt(upload, transcript.source, transcript.started,
                    prompt_versions.path_for(version, prompt_versions.CHAT_DIR), question)
                prepared.append(dict(id=uuid.uuid4().hex, title=question.splitlines()[0][:100], question=question,
                    excerpt=payload.get('excerpt', ''), config=config,
                    request=dict(prompt=prompt, attachments=attachments,
                                 ancestor_ids=[node['id'] for node in ancestors])))
            return self.store.add_children(cid, parent, prepared)

    def create_chatgpt_branch(self, cid, nid):
        from caption.chatgpt_branch import create_branch, source_conversation_id
        with self.service.lock:
            node = self.store.get(cid, nid)
            if node['status'] != 'done':
                raise ValueError('Chờ câu trả lời của node cha hoàn tất trước khi tạo nhánh ChatGPT.')
            url, position = node['response_conversation_url'], node['response_position']
            source_conversation_id(url, position)
            if self.service.busy:
                raise BusyError('ChatGPT đang xử lý yêu cầu khác. Hãy đợi xong rồi tạo nhánh.')
            self.service.busy = True
        try:
            return create_branch(url, position)
        finally:
            with self.service.lock:
                self.service.busy = False

    def cancel(self, cid, nid):
        with self.service.lock:
            node = self.store.get(cid, nid)
            if node['status'] == 'queued':
                job = self.service.jobs.get('research-' + nid)
                if job:
                    self.service.update_job(job, status='cancelled')
                return self.store.update(cid, nid, status='cancelled', detail='Đã hủy trước khi gửi.')
            if node['status'] == 'working':
                self.service.job_cancel['research-' + nid].set()
            return self.store.get(cid, nid)

    def retry(self, cid, nid, selected_tab=None):
        with self.service.lock:
            node = self.store.get(cid, nid, private=True)
            if node['status'] not in ('error', 'cancelled', 'interrupted'):
                raise ValueError('Only failed, cancelled or interrupted branches can be retried.')
            config = node['config']
            if selected_tab:
                config['selected_tab'] = selected_tab
            return self.store.update(cid, nid, status='queued', detail='', partial='', config=config)

    def delete(self, cid, nid):
        with self.service.lock:
            result = self.store.delete_subtree(cid, nid)
            for node_id in result['ids']:
                jid = 'research-' + node_id
                cancel = self.service.job_cancel.get(jid)
                if cancel:
                    cancel.set()
                job = self.service.jobs.get(jid)
                if job:
                    self.service.update_job(job, status='cancelled', detail='Node đã bị xóa.', partial='')
            return result

    def work(self):
        while not self.stop.wait(.25):
            try:
                with self.service.lock:
                    if self.service.busy or self.service.closed:
                        continue
                    node = self.store.next_queued()
                    if not node or not self.store.claim(node['conversation_id'], node['id']):
                        continue
                    self.service.busy = True
                    job = dict(id='research-' + node['id'], node_id=node['id'], conversation_id=node['conversation_id'],
                               kind='research', status='working', detail='Preparing…', partial='', error='', result=None)
                    self.service.jobs[job['id']] = job
                    self.service.job_cancel[job['id']] = threading.Event()
                self.run(node, job)
            except Exception:
                log.exception('Research queue failed')

    def run(self, node, job):
        cid, nid = node['conversation_id'], node['id']
        cancel = self.service.job_cancel[job['id']]
        config, request = node['config'], node['request']
        def finish(**values):
            # Deleting a running subtree must never resurrect it when Chrome returns.
            try:
                self.store.update(cid, nid, **values)
                return True
            except KeyError:
                return False
        def progress(**values):
            with self.service.lock:
                if not cancel.is_set():
                    self.store.progress(cid, nid, **values)
                    self.service.update_job(job, **values)
        try:
            with ProcessLock(paths.CACHE_DIR / 'web-job.lock'):
                if cancel.is_set() or self.stop.is_set():
                    raise chatgpt.ChatGPTCancelled(chatgpt.CANCELLED_MESSAGE)
                # Avoid restoring large uploads repeatedly while native ChatGPT is busy.
                # ask() still acquires its own lock before touching Chrome.
                with ProcessLock(paths.CACHE_DIR / 'chatgpt-request.lock'):
                    pass
                attachments = self.store.restore_attachments(cid, nid, paths.CACHE_DIR / 'research-uploads' / nid)
                response_tab = {}
                response_source = {}
                result = chatgpt.ask(request['prompt'], attachments,
                    lambda text: progress(detail=text),
                    new_chat=config.get('new_chat', False), selected_tab=config['selected_tab'], tab_name='rtc-research',
                    power=None if config['power'] < 0 else config['power'], cancel_event=cancel,
                    on_partial=lambda text: progress(partial=text),
                    on_response_tab=lambda title: response_tab.update(title=title),
                    on_response_source=response_source.update)
                with self.service.lock:
                    if cancel.is_set():
                        raise chatgpt.ChatGPTCancelled(chatgpt.CANCELLED_MESSAGE)
                    saved = finish(status='done', answer=result, partial='', detail='Completed',
                                   response_tab_title=response_tab.get('title', ''), **response_source)
                    self.service.update_job(job, status='done' if saved else 'cancelled', partial='',
                                            detail='Completed' if saved else 'Node đã bị xóa.')
        except BusyError:
            # The native UI owns Chrome. Nothing has been sent; leave this queued.
            status = 'cancelled' if cancel.is_set() else 'queued'
            finish(status=status, detail='Đang chờ thao tác ChatGPT khác hoàn tất.')
            self.service.update_job(job, status='cancelled' if status == 'cancelled' else 'waiting')
        except Exception as exc:
            status = 'cancelled' if cancel.is_set() or self.stop.is_set() else 'error'
            finish(status=status, detail=str(exc), partial=job['partial'])
            self.service.update_job(job, status=status, error=str(exc))
        finally:
            with self.service.lock:
                self.service.busy = False

    def close(self):
        self.stop.set()
        with self.service.lock:
            for jid, cancel in self.service.job_cancel.items():
                if jid.startswith('research-'):
                    cancel.set()
        self.thread.join(timeout=6)
