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

from caption import chatgpt, livecaptions, paths, prompt_versions, vocab
from caption.conversations import ConversationStore, ConversationTranscript
from caption.settings import Settings
from caption.shared_access import ProcessLock, capture_lock
from caption.translation import Config, Translator, download_offline
from caption.translation_store import TranslationStore
from caption.translation_worker import TranslationWorker
from caption.translation_batch import SentenceBatcher
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
        self.capture = dict(active=False, conversation_id=None, partial='', status='Ready', error='')
        self.jobs = {}
        self.job_cancel = {}
        self.busy = False
        self.closed = False
        self.translation_preview = ''
        self.preview_revision = 0
        self.preview_timer = None
        self.preview_snapshot = None
        self.last_final = ''
        self.final_revision = 0
        self.batcher = SentenceBatcher(self.schedule, lambda timer: timer.cancel(), self.submit_batch, self.translation_error)
        self.overlay_process = None
        self._stop = threading.Event()
        self._events = threading.Thread(target=self.drain, name='web-translation-events', daemon=True)
        self._events.start()
        from .research import Research
        self.research = Research(self)

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
            return dict(capture=dict(self.capture), jobs=[dict(j) for j in self.jobs.values()],
                        translation_preview=self.translation_preview, last_final=self.last_final,
                        final_revision=self.final_revision)

    def start(self, cid):
        with self.lock:
            if self.reader:
                raise ValueError('Pause the active recording before starting another one.')
            transcript = self.transcript_for(cid)
            transcript.begin_segment(livecaptions.LABEL)
            try:
                self.store.set_status(cid, 'recording')
                self.transcript = transcript
                self.capture = dict(active=True, conversation_id=cid, partial='', status='Connecting to Windows Live Captions…', error='')
                self.reader = livecaptions.LiveCaptionsReader(self.on_caption)
                from caption.livecaptions_settings import apply_saved
                self.reader.configure = lambda: apply_saved(self.store)
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
                elif event.kind == 'error':
                    self.capture['error'] = event.text
                    self.capture['status'] = event.text
                    if self.reader:
                        self.reader.stop()
                elif event.kind == 'partial':
                    self.capture['partial'] = event.text
                    self.preview_revision += 1
                    self.translation_preview = ''
                    self.worker.cancel_preview()
                    settings = Settings.load()
                    if event.text and settings.translation_enabled and settings.translation_partial:
                        self.preview_snapshot = (self.capture['conversation_id'], event.text, Config.from_settings(settings), self.preview_revision)
                        if self.preview_timer is None:
                            self.preview_timer = self.schedule(500, self.send_preview)
                elif event.kind == 'final' and self.transcript:
                    self.preview_revision += 1
                    self.preview_snapshot = None
                    self.translation_preview = ''
                    self.worker.cancel_preview()
                    self.last_final = event.text
                    self.final_revision += 1
                    self.on_lines(self.transcript.write(event.text, event.end_of_utterance))
                elif event.kind == 'stopped':
                    try:
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

    def on_lines(self, captured):
        settings = Settings.load()
        for origin, stamp, text in captured:
            line = self.translations.line(self.capture['conversation_id'], origin, stamp, text)
            if settings.translation_enabled:
                self.batcher.add(line, Config.from_settings(settings), settings.translation_batch_size, settings.translation_batch_timeout)

    def send_preview(self):
        self.preview_timer = None
        if self.preview_snapshot:
            try:
                self.worker.preview(*self.preview_snapshot)
            except Exception as exc:
                self.translation_error(exc)

    def submit_batch(self, lines, config):
        self.worker.submit_batch(lines, config)

    def translation_error(self, exc):
        self.capture['error'] = str(exc)

    def drain(self):
        while not self._stop.wait(.2):
            try:
                while True:
                    task, status, value = self.worker.results.get_nowait()
                    with self.lock:
                        if task.preview and task.revision == self.preview_revision and status == 'done':
                            self.translation_preview = value
                        if status == 'error':
                            self.capture['error'] = value
            except queue.Empty:
                pass

    def translate(self, cid, ids=None, force=False):
        self.translations.sync_transcript(self.transcript_for(cid))
        settings = Settings.load()
        config = Config.from_settings(settings)
        config.validate()
        lines = self.translations.lines(cid) if ids else self.translations.missing(cid, config)
        if ids:
            lines = [line for line in lines if line['id'] in ids]
        count = max(1, int(settings.translation_batch_size))
        self.worker.resume()
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
            self.update_job(job, status='cancelled' if cancel.is_set() else 'error', error=str(exc), detail='Request stopped')
        finally:
            with self.lock:
                job['_retry'] = payload
                self.busy = False

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
            if kind == 'chat' and settings.chat_screenshot and not any(p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') for p in attachments):
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
        self.research.close()
        self.pause()
        reader = self.reader
        if reader:
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

    def hide_overlay(self):
        if self.overlay_process and self.overlay_process.poll() is None:
            try:
                # EOF lets Tk shut down normally and persist the overlay position.
                if not self.overlay_process.stdin.closed:
                    self.overlay_process.stdin.close()
            except OSError:
                pass
