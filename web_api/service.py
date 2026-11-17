from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from caption import chatgpt, chatgpt_dictation, livecaptions, paths, prompt_versions, vocab
from caption.conversations import ConversationStore, ConversationTranscript
from caption.settings import Settings
from caption.shared_access import ProcessLock, capture_lock
from caption.translation import Config, Translator, download_offline
from caption.translation_store import TranslationStore
from caption.translation_worker import TranslationWorker
from caption.translation_batch import SentenceBatcher
from caption.realtime_translation import RealtimePassage
from caption.voca_client import VocaClient

log = logging.getLogger(__name__)


class Service:
    def __init__(self):
        self.store = ConversationStore()
        self.translations = TranslationStore(self.store.db_path)
        self.translations.recover()
        self.worker = TranslationWorker(self.translations)
        self.lock = threading.RLock()
        self.reader = self.transcript = None
        self.capture = dict(active=False, conversation_id=None, partial='', status='Ready', error='', warning='')
        self.jobs = {}
        self.job_cancel = {}
        self.busy = False
        self.closed = False
        self.translation_preview = ''
        self.translation_issue = ''
        self.preview_revision = 0
        self.preview_timer = None
        self.preview_snapshot = None
        self.preview_context = None
        self.realtime_passage = RealtimePassage()
        self.last_final = ''
        self.final_revision = 0
        self.batcher = SentenceBatcher(self.schedule, lambda timer: timer.cancel(), self.submit_batch, self.translation_error)
        self.overlay_process = None
        self._stop = threading.Event()
        self._events = threading.Thread(target=self.drain, name='web-translation-events', daemon=True)
        self._events.start()
        from .research import Research
        self.research = Research(self)
        from caption.livecaptions_settings import PreferenceRestorer
        self.captions_preferences = PreferenceRestorer(self.store)
        self.captions_preferences.start()

    def schedule(self, ms, callback):
        def run():
            with self.lock:
                if not self.closed:
                    callback()
        timer = threading.Timer(ms / 1000, run)
        timer.daemon = True
        timer.start()
        return timer

    def conversation(self, cid):
        item = self.store.get(cid)
        if not item:
            raise KeyError('Conversation not found')
        return item

    def transcript_for(self, cid):
        return ConversationTranscript(self.conversation(cid), self.store)

    def state(self):
        with self.lock:
            source = self.capture.get('source') if self.capture['active'] else Settings.load().web_capture_source
            return dict(capture={**self.capture, 'source': source,
                        'warning': self.capture['warning'] or (self.captions_preferences.warning if source == 'windows' else '')}, jobs=[dict(j) for j in self.jobs.values()],
                        translation_preview=self.translation_preview, translation_error=self.translation_issue, last_final=self.last_final,
                        final_revision=self.final_revision, overlay_visible=self.overlay_visible())

    def start(self, cid, return_tab=''):
        with self.lock:
            if self.reader:
                raise ValueError('Pause the active recording before starting another one.')
            self.clear_preview()
            settings = Settings.load()
            chatgpt_dictation.validate_source(settings.web_capture_source)
            source = settings.web_capture_source
            transcript = self.transcript_for(cid)
            transcript.begin_segment(chatgpt_dictation.LABEL if source == 'chatgpt' else livecaptions.LABEL)
            try:
                self.store.set_status(cid, 'recording')
                self.transcript = transcript
                if self.capture['conversation_id'] != cid:
                    self.last_final = ''
                self.capture = dict(active=True, conversation_id=cid, partial='', source=source,
                    status='Đang mở tab thu âm ChatGPT…' if source == 'chatgpt' else 'Connecting to Windows Live Captions…', error='', warning='')
                self.reader = (chatgpt_dictation.ChatGPTDictationReader(self.on_caption, return_tab)
                    if source == 'chatgpt' else livecaptions.LiveCaptionsReader(self.on_caption))
                self.reader.start()
            except Exception:
                transcript.close()
                self.reader = self.transcript = None
                self.capture['active'] = False
                raise
        return self.state()

    def pause(self):
        with self.lock:
            if self.reader:
                self.capture['status'] = 'Stopping…'
                self.reader.stop()
        return self.state()

    def on_caption(self, event):
        with self.lock:
            try:
                if event.kind in ('ready', 'status'):
                    self.capture['status'] = event.text
                elif event.kind == 'warning':
                    self.capture['warning'] = event.text
                elif event.kind == 'error':
                    self.capture['error'] = event.text
                    self.capture['status'] = event.text
                    if self.reader:
                        self.reader.stop()
                elif event.kind == 'partial':
                    self.capture['partial'] = event.text
                    self.update_preview(Settings.load())
                elif event.kind == 'final' and self.transcript:
                    if self.preview_context:
                        self.realtime_passage.final(event.text)
                    self.last_final = event.text
                    self.final_revision += 1
                    self.on_lines(self.transcript.write(event.text, event.end_of_utterance))
                elif event.kind == 'stopped':
                    try:
                        self.clear_preview()
                        if self.transcript:
                            self.on_lines(self.transcript.close())
                        self.batcher.flush()
                    finally:
                        self.capture.update(active=False, partial='', status=self.capture['error'] or 'Paused')
                        self.transcript = self.reader = None
            except Exception as exc:
                log.exception('Live Captions event failed')
                self.capture['error'] = str(exc)
                if self.reader:
                    self.reader.stop()
                return False
            return True

    def on_lines(self, captured):
        settings = Settings.load()
        google = settings.translation_enabled and settings.translation_engine == 'google_web'
        for origin, stamp, text in captured:
            line = self.translations.line(self.capture['conversation_id'], origin, stamp, text)
            if settings.translation_enabled and not google:
                try:
                    self.batcher.add(line, Config.from_settings(settings), settings.translation_batch_size, settings.translation_batch_timeout)
                except Exception as exc:
                    self.translation_error(exc)
        if google and captured:
            try:
                self.worker.sync_google(self.capture['conversation_id'], Config.from_settings(settings))
            except Exception as exc:
                self.translation_error(exc)

    def clear_preview(self, reset_passage=True):
        """Cancel live work; persisted passages are never cleared by final jobs."""
        if reset_passage:
            self.realtime_passage = RealtimePassage()
        self.preview_revision += 1
        self.preview_snapshot = self.preview_context = None
        if self.preview_timer:
            self.preview_timer.cancel()
            self.preview_timer = None
        self.translation_preview = ''
        self.worker.cancel_preview()

    def update_preview(self, settings):
        text = self.capture['partial'].strip()
        if not (self.capture['active'] and self.transcript and settings.translation_enabled
                and settings.translation_partial and settings.translation_engine == 'chrome'):
            self.clear_preview()
            return
        config = Config.from_settings(settings)
        context = (self.capture['conversation_id'], config.fingerprint)
        if context != self.preview_context:
            self.clear_preview()
            self.preview_context = context
        if self.realtime_passage.advance(text):
            self.clear_preview(reset_passage=False)
            self.preview_context = context
        combined = self.realtime_passage.source(text)
        if not combined:
            return
        snapshot = (self.capture['conversation_id'], combined, config, self.preview_revision)
        if snapshot == self.preview_snapshot:
            return
        self.preview_snapshot = snapshot
        # Throttle rather than debounce: continuous speech must still be sent.
        # Keep the in-flight result; the worker coalesces pending drafts.
        if self.preview_timer is None:
            self.preview_timer = self.schedule(300, self.send_preview)

    def send_preview(self):
        self.preview_timer = None
        if self.preview_snapshot:
            settings = Settings.load()
            context = (self.capture['conversation_id'], Config.from_settings(settings).fingerprint)
            if not (self.capture['active'] and settings.translation_enabled and settings.translation_partial
                    and settings.translation_engine == 'chrome' and context == self.preview_context):
                self.clear_preview()
                return
            try:
                self.worker.preview(*self.preview_snapshot)
            except Exception as exc:
                self.translation_error(exc)

    def submit_batch(self, lines, config):
        try:
            self.worker.submit_batch(lines, config)
        except Exception as exc:
            self.translation_error(exc)

    def translation_error(self, exc):
        # Keep the original provider error, not a subsequent "worker paused".
        if not self.translation_issue:
            self.translation_issue = str(exc)

    def drain(self):
        while not self._stop.wait(.2):
            try:
                while True:
                    task, status, value = self.worker.results.get_nowait()
                    with self.lock:
                        current_preview = (task.revision == self.preview_revision
                            and task.line['conversation_id'] == self.capture['conversation_id']
                            and self.capture['active'])
                        if task.preview and current_preview and status == 'done':
                            try:
                                self.translations.save_realtime(task.line['conversation_id'], self.realtime_passage.key,
                                    task.config, task.line['source_text'], value)
                            except Exception as exc:
                                log.exception('Could not save realtime translation')
                                self.translation_error(exc)
                                continue
                            self.realtime_passage.rendered_source = task.line['source_text']
                            self.translation_preview = value
                            if not self.capture['partial'].strip():
                                self.update_preview(Settings.load())
                        if status == 'error' and (not task.preview or current_preview):
                            self.translation_issue = value
                        elif status == 'done' and (not task.preview or current_preview):
                            self.translation_issue = ''
            except queue.Empty:
                pass

    def translate(self, cid, ids=None, force=False):
        self.translations.sync_transcript(self.transcript_for(cid))
        settings = Settings.load()
        config = Config.from_settings(settings)
        config.validate()
        if config.engine == 'google_web':
            self.worker.resume()
            self.translation_issue = ''
            submitted = self.worker.sync_google(cid, config, force=force)
            return {'batches': int(submitted), 'lines': len(self.translations.lines(cid))}
        lines = self.translations.lines(cid) if ids else self.translations.missing(cid, config)
        if ids:
            lines = [line for line in lines if line['id'] in ids]
        count = max(1, int(settings.translation_batch_size))
        self.worker.resume()
        self.translation_issue = ''
        submitted = sum(bool(self.worker.submit_batch(lines[i:i+count], config, force=force)) for i in range(0, len(lines), count))
        return {'batches': submitted, 'lines': len(lines)}

    def create_job(self, cid, kind, payload):
        with self.lock:
            if self.busy:
                raise ValueError('Wait for the current ChatGPT request, or cancel it first.')
            self.conversation(cid)
            job = dict(id=uuid.uuid4().hex, conversation_id=cid, kind=kind, status='queued',
                       detail='Preparing…', partial='', error='', result=None)
            self.jobs[job['id']] = job
            self.job_cancel[job['id']] = threading.Event()
            self.busy = True
            # Retries reuse the frozen request, uploads and prompt, never resend automatically.
            threading.Thread(target=self.run_job, args=(job, dict(payload)), daemon=True, name='web-chatgpt').start()
            return dict(job)

    def update_job(self, job, **values):
        with self.lock:
            job.update(values)

    def run_job(self, job, payload):
        cancel = self.job_cancel[job['id']]
        try:
            with ProcessLock(paths.CACHE_DIR / 'web-job.lock'):
                self.update_job(job, status='working')
                settings = Settings.load()
                cid, kind = job['conversation_id'], job['kind']
                if kind == 'translation-test':
                    result = Translator().translate('Hello, this is a translation test.', Config.from_settings(settings), cancel)
                elif kind == 'translation-download':
                    download_offline()
                    result = 'Offline model downloaded.'
                elif kind == 'voca-save':
                    result = self.save_vocab(payload['words'], settings)
                    self.store.add_message(cid, 'bot', 'Vocabulary · saved to Voca', json.dumps(result, ensure_ascii=False), kind='vocab')
                else:
                    prepared = payload.get('_prepared') or self.prepare_request(cid, kind, payload, settings)
                    payload['_prepared'] = prepared
                    if cancel.is_set():
                        raise chatgpt.ChatGPTCancelled(chatgpt.CANCELLED_MESSAGE)
                    response_tab = {}
                    response_source = {}
                    result = chatgpt.ask(prepared['prompt'], [Path(x) for x in prepared['attachments']],
                        lambda text: self.update_job(job, detail=text),
                        new_chat=prepared['new_chat'], tab_name=prepared['tab_name'],
                        selected_tab=prepared['selected_tab'], power=prepared['power'], cancel_event=cancel,
                        on_partial=lambda text: self.update_job(job, partial=text),
                        on_response_tab=lambda title: response_tab.update(title=title),
                        on_response_source=response_source.update)
                    if kind == 'vocab':
                        words = [vocab.NewWord(**w) for w in prepared['words']]
                        vocab.parse_answer(result, words)
                        result = [asdict(w) for w in words]
                        mid = self.store.add_message(cid, 'bot', 'Vocabulary', json.dumps(result, ensure_ascii=False), kind='vocab',
                                                     response_tab_title=response_tab.get('title', ''), **response_source)
                        if payload.get('save_voca') and settings.voca_api_key:
                            result = self.save_vocab(result, settings)
                            self.store.update_message_content(cid, mid, json.dumps(result, ensure_ascii=False))
                    else:
                        self.store.add_message(cid, 'bot', 'ChatGPT', result, kind=kind,
                                               response_tab_title=response_tab.get('title', ''), **response_source)
                        if kind == 'summary':
                            with (self.conversation(cid).folder / 'summaries.md').open('a', encoding='utf-8') as stream:
                                stream.write(f'<!-- ChatGPT summary, {datetime.now():%Y-%m-%d %H:%M:%S} -->\n\n{result}\n\n---\n\n')
                self.update_job(job, status='done', partial='', result=result, detail='Completed')
        except Exception as exc:
            log.exception('Web job failed')
            message = str(exc)
            if job['kind'] == 'chat' and isinstance(exc, chatgpt.ChatGPTCopyError) and not cancel.is_set():
                mid = self.store.add_message(job['conversation_id'], 'bot', 'ChatGPT', '',
                    response_tab_title=exc.title, **exc.source)
                self.update_job(job, answer_id=mid)
                message = 'ChatGPT chưa copy được nội dung. Bấm “Lấy lại câu trả lời” ở câu trả lời trống để đọc lại, không gửi lại câu hỏi.'
            if job['kind'] == 'chat' and isinstance(exc, chatgpt.ChatGPTTabClosed) and not cancel.is_set():
                message = self.recover_chat_tab(job, payload)
            self.update_job(job, status='cancelled' if cancel.is_set() else 'error', error=message, detail='Request stopped')
        finally:
            with self.lock:
                job['_retry'] = payload
                self.busy = False

    def recover_chat_tab(self, job, payload):
        """Repair only the selection. The failed question requires manual Retry."""
        self.update_job(job, error_code='chatgpt_tab_closed')
        prepared = payload.get('_prepared', {})
        old_key = prepared.get('selected_tab') or payload.get('selected_tab', '')
        try:
            tabs = chatgpt.list_chrome_chatgpt_tabs()
            chosen = tabs[0] if tabs else None
            settings = Settings.load()
            settings.chatgpt_selected_tab = chosen.key if chosen else ''
            settings.chatgpt_selected_tab_title = chosen.title if chosen else ''
            settings.save()
            self.update_job(job, tab_recovery={
                'old_key': old_key, 'selected_tab': settings.chatgpt_selected_tab,
                'selected_tab_title': settings.chatgpt_selected_tab_title,
                'tabs': [asdict(tab) for tab in tabs],
            })
            # Retain the frozen prompt/uploads and change only the destination
            # for a later user-initiated Retry. Never create another job here.
            payload['selected_tab'] = settings.chatgpt_selected_tab
            prepared['selected_tab'] = settings.chatgpt_selected_tab
            if chosen:
                return (f'Tab ChatGPT cũ không được open, hiện tại đang chọn tab này: {chosen.title}. '
                        'Câu hỏi chưa được gửi lại. Bấm Retry nếu bạn muốn gửi lại.')
            return ('Tab ChatGPT cũ không còn mở và không tìm thấy tab ChatGPT nào đang mở. '
                    'Hãy mở hoặc chọn một tab rồi gửi lại câu hỏi. App chưa gửi lại câu hỏi.')
        except Exception as exc:
            log.exception('Could not refresh ChatGPT tabs after a missing selection')
            return f'Tab ChatGPT cũ không còn mở. Không quét lại được danh sách tab: {exc}. Câu hỏi chưa được gửi lại.'

    def prepare_request(self, cid, kind, payload, settings):
        transcript = self.transcript_for(cid)
        attachments = []
        words = []
        selected_tab = payload.get('selected_tab') or (settings.summary_chatgpt_selected_tab if kind == 'summary' else settings.chatgpt_selected_tab)
        if kind == 'vocab':
            words = [vocab.NewWord(w) for w in vocab.parse_input(payload.get('message', ''))]
            if not words or len(words) > 100:
                raise ValueError('Enter between 1 and 100 words.')
            for word in words:
                word.sentence, word.time = vocab.find_context(transcript.path, word.word)
            prompt = vocab.build_prompt(words)
            selected_tab = None
            power = settings.vocab_chatgpt_power
        else:
            if not selected_tab:
                raise ValueError('Choose a ChatGPT Chrome tab first.')
            prompt_dir = prompt_versions.SUMMARY_DIR if kind == 'summary' else prompt_versions.CHAT_DIR
            version = prompt_versions.resolve(payload.get('prompt') or getattr(settings, f'{kind}_prompt'), prompt_dir)
            if not version:
                raise ValueError('No prompt version found.')
            upload = None
            attach = payload.get('attach_transcript', settings.summary_attach_transcript if kind == 'summary' else False)
            if attach:
                if not transcript.has_text:
                    raise ValueError('This conversation has no saved captions yet.')
                original = transcript.snapshot(int(payload.get('max_lines', getattr(settings, f'{kind}_max_lines'))))
                snapshot_dir = paths.CACHE_DIR / 'web-uploads' / uuid.uuid4().hex
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                upload = snapshot_dir / original.name
                shutil.copy2(original, upload)
                attachments.append(upload)
            prompt = chatgpt.build_prompt(upload, transcript.source, transcript.started,
                prompt_versions.path_for(version, prompt_dir), payload.get('message', '') if kind == 'chat' else '')
            if kind == 'chat' and payload.get('attach_history', settings.chat_attach_history):
                history = self.store.chat_context_snapshot(cid)
                if history:
                    attachments.append(history)
            for name in payload.get('files', []):
                attachments.append(self.file_path(cid, name))
            if kind == 'chat' and payload.get('screenshot', settings.chat_screenshot) and not any(p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') for p in attachments):
                from .screen import capture
                shot = capture(self.conversation(cid).screenshots_dir)
                relative = shot.relative_to(self.conversation(cid).folder).as_posix()
                payload['files'] = [*payload.get('files', []), relative]
                attachments.append(shot)
            power = getattr(settings, f'{kind}_chatgpt_power')
        power = int(payload.get('power', power))
        if power not in (-1, 0, 1, 2):
            raise ValueError('Invalid ChatGPT power.')
        self.store.add_message(cid, 'user', 'You', payload.get('message') or 'Summarize this conversation',
            kind=kind, files=tuple(self.file_path(cid, name) for name in payload.get('files', [])))
        return dict(prompt=prompt, attachments=[str(p) for p in attachments], words=[asdict(w) for w in words],
                    selected_tab=selected_tab, power=None if power < 0 else power,
                    tab_name=chatgpt.VOCAB_TAB if kind == 'vocab' else f'{chatgpt.APP_TAB}-{cid}',
                    new_chat=payload.get('new_chat', settings.summary_new_chat if kind == 'summary' else False))

    def file_path(self, cid, name):
        folder = self.conversation(cid).folder.resolve()
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or path == folder or not path.is_file():
            raise ValueError('Attachment not found in this conversation.')
        return path

    def save_vocab(self, words, settings):
        if not settings.voca_api_key:
            raise ValueError('Enter your Voca API key in Settings.')
        values = [vocab.NewWord(**word) for word in words]
        kwargs = {'source': vocab.VOCA_SOURCE}
        if settings.voca_collection_id:
            kwargs['collection_id'] = settings.voca_collection_id
        report = VocaClient(api_key=settings.voca_api_key).add_words(vocab.voca_items(values, 'Real-time Caption'), **kwargs)
        for item in report['results']:
            word = values[item['index']]
            word.voca_status = item.get('status', '')
            word.voca_error = (item.get('error') or {}).get('message', '')
        return [asdict(w) for w in values]

    def close(self):
        self.captions_preferences.close()
        self.research.close()
        self.pause()
        reader = self.reader
        if reader:
            if hasattr(reader, 'shutdown'):
                reader.shutdown()
            reader._thread.join(timeout=6)
        with self.lock:
            self.closed = True
            self.batcher.clear()
            if self.preview_timer:
                self.preview_timer.cancel()
            for cancel in self.job_cancel.values():
                cancel.set()
            self.worker.close()
            self._stop.set()
            self.hide_overlay()
        self.worker.join(timeout=2)

    def hide_overlay(self):
        if self.overlay_process and self.overlay_process.poll() is None:
            try:
                # EOF lets Tk shut down normally and persist the overlay position.
                if not self.overlay_process.stdin.closed:
                    self.overlay_process.stdin.close()
            except OSError:
                pass

    def overlay_visible(self):
        process = self.overlay_process
        return bool(process and process.poll() is None and process.stdin and not process.stdin.closed)
