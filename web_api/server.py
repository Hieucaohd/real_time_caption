from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, fields
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, create_model, ConfigDict

from caption import paths, prompt_versions, chatgpt
from caption.settings import Settings
from caption.shared_access import BusyError, capture_lock
from caption.translation import ENGINES, LANGUAGES, Config, TranslationError
from caption.translation_batch import batch_options
from caption.transcript_export import last_lines
from .service import Service

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'web' / 'dist'
SettingsPatch = create_model('SettingsPatch', __config__=ConfigDict(extra='forbid', strict=True),
    **{f.name: (type(f.default) | None, None) for f in fields(Settings)})


class ConversationInput(BaseModel):
    title: str = Field(default='New conversation', min_length=1, max_length=200)


class PromptInput(BaseModel):
    name: str = ''
    text: str = Field(min_length=1, max_length=200_000)
    overwrite: bool = False


class JobInput(BaseModel):
    kind: str = 'chat'
    message: str = Field(default='', max_length=100_000)
    selected_tab: str = ''
    prompt: str = ''
    power: int | None = Field(default=None, ge=-1, le=2)
    max_lines: int | None = Field(default=None, ge=0, le=1_000_000)
    attach_transcript: bool | None = None
    attach_history: bool | None = None
    new_chat: bool | None = None
    files: list[str] = Field(default_factory=list, max_length=20)
    words: list[dict] = Field(default_factory=list, max_length=100)
    save_voca: bool = False


class TranslateInput(BaseModel):
    ids: list[int] | None = None
    force: bool = False


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or Service()
        yield
        app.state.service.close()

    app = FastAPI(title='Real-time Caption', lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        if request.url.hostname not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': 'Local access only'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if request.headers.get('x-rtc-client') != 'web' or (origin and urlparse(origin).netloc != request.url.netloc):
                return JSONResponse({'detail': 'Open the app from its local URL.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.exception_handler(ValueError)
    @app.exception_handler(TranslationError)
    @app.exception_handler(chatgpt.ChatGPTError)
    async def invalid(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(BusyError)
    async def busy(_, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(Exception)
    async def failure(_, exc):
        logging.getLogger(__name__).exception('Web request failed')
        return JSONResponse({'detail': 'Operation failed. See caption-web.log for details.'}, status_code=500)

    def svc(request):
        return request.app.state.service

    @app.get('/api/health')
    def health():
        return {'app': 'real-time-caption-web', 'data_dir': str(paths.DATA_DIR), 'version': 1}

    @app.get('/api/capture/state')
    def capture_state(request: Request):
        state = svc(request).state()
        return {key: state[key] for key in ('capture', 'last_final', 'final_revision')}

    @app.get('/api/bootstrap')
    def bootstrap(request: Request):
        s = svc(request)
        state = s.state()
        state['jobs'] = [{k: v for k, v in job.items() if not k.startswith('_')} for job in state['jobs']]
        return {**state, 'conversations': [asdict(c) for c in s.store.list()],
                'settings': asdict(Settings.load()), 'engines': ENGINES, 'languages': LANGUAGES,
                'prompts': {kind: prompt_versions.list_versions(folder) for kind, folder in
                            [('chat', prompt_versions.CHAT_DIR), ('summary', prompt_versions.SUMMARY_DIR)]}}

    @app.get('/api/conversations/{cid}')
    def detail(cid: str, request: Request):
        s = svc(request)
        transcript = s.transcript_for(cid)
        s.translations.sync_transcript(transcript)
        messages = []
        for message in s.store.messages(cid):
            value = asdict(message)
            # Browser math uses dollar delimiters; stored Markdown stays byte-for-byte intact.
            value['markdown'] = browser_markdown(message.content) if message.role == 'bot' else message.content
            if message.attachment:
                p = Path(message.attachment)
                if p.is_absolute():
                    try:
                        value['attachment'] = p.relative_to(transcript.folder).as_posix()
                    except ValueError:
                        value['attachment'] = None
            messages.append(value)
        lines = s.translations.lines(cid)
        return {'conversation': asdict(s.conversation(cid)), 'transcript': transcript.read_text(),
                'messages': messages, 'lines': lines}

    @app.post('/api/conversations')
    def create(body: ConversationInput, request: Request):
        from caption.livecaptions import LABEL
        return asdict(svc(request).store.create(LABEL, body.title))

    @app.patch('/api/conversations/{cid}')
    def rename(cid: str, body: ConversationInput, request: Request):
        if svc(request).busy:
            raise ValueError('Wait for the current request before renaming.')
        return asdict(svc(request).store.rename(cid, body.title))

    @app.post('/api/conversations/{cid}/capture')
    def start(cid: str, request: Request):
        return svc(request).start(cid)

    @app.post('/api/capture/pause')
    def pause(request: Request):
        return svc(request).pause()

    @app.get('/api/conversations/{cid}/transcript')
    def transcript(cid: str, request: Request, count: int = 0):
        text = last_lines(svc(request).transcript_for(cid).read_text(), max(0, count))
        return Response(text, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="transcript.txt"'})

    @app.post('/api/conversations/{cid}/open-folder')
    def open_folder(cid: str, request: Request):
        os.startfile(svc(request).conversation(cid).folder)
        return {'ok': True}

    @app.get('/api/conversations/{cid}/files/{name:path}')
    def file(cid: str, name: str, request: Request):
        path = svc(request).file_path(cid, name)
        return FileResponse(path, filename=path.name)

    @app.post('/api/conversations/{cid}/files')
    async def upload(cid: str, request: Request, file: UploadFile):
        folder = svc(request).conversation(cid).folder / 'attachments'
        folder.mkdir(exist_ok=True)
        name = re.sub(r'[^\w. -]', '_', (file.filename or 'attachment').replace('\\', '/').split('/')[-1])[:100]
        path = folder / f'{uuid.uuid4().hex[:12]}_{name}'
        size = 0
        try:
            with path.open('xb') as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise ValueError('Each attachment must be at most 50 MB.')
                    stream.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return {'name': path.name, 'path': 'attachments/' + path.name, 'size': size}

    @app.post('/api/conversations/{cid}/screenshot')
    def screenshot(cid: str, request: Request):
        from PIL import ImageGrab
        from caption.screenshot import save
        path = save(ImageGrab.grab(), svc(request).conversation(cid).screenshots_dir)
        return {'path': 'screenshots/' + path.name, 'name': path.name}

    @app.patch('/api/settings')
    def settings(body: SettingsPatch, request: Request):
        current = Settings.load()
        values = body.model_dump(exclude_none=True)
        for key, value in values.items():
            if key.endswith('_max_lines') and not 0 <= value <= 1_000_000:
                raise ValueError('Line count must be between 0 and 1000000.')
            if key.endswith('_power') and value not in (-1, 0, 1, 2):
                raise ValueError('Invalid ChatGPT power.')
            setattr(current, key, value)
        batch_options(current.translation_batch_size, current.translation_batch_timeout)
        if current.translation_engine not in ENGINES:
            raise ValueError('Invalid translation engine.')
        if not 8 <= current.overlay_font_size <= 96 or not 1 <= current.overlay_lines <= 10 or not .1 <= current.overlay_alpha <= 1:
            raise ValueError('Invalid overlay settings.')
        current.save()
        if values.get('translation_enabled'):
            svc(request).worker.resume()
        return asdict(Settings.load())

    @app.get('/api/chrome-tabs')
    def tabs():
        return [asdict(tab) for tab in chatgpt.list_chrome_chatgpt_tabs()]

    def prompt_dir(kind):
        if kind not in ('chat', 'summary'):
            raise ValueError('Unknown prompt type.')
        return prompt_versions.CHAT_DIR if kind == 'chat' else prompt_versions.SUMMARY_DIR

    @app.get('/api/prompts/{kind}')
    def prompt(kind: str, name: str = ''):
        folder = prompt_dir(kind)
        chosen = prompt_versions.resolve(name, folder)
        return {'name': chosen, 'text': prompt_versions.read(chosen, folder) if chosen else ''}

    @app.post('/api/prompts/{kind}')
    def save_prompt(kind: str, body: PromptInput):
        folder = prompt_dir(kind)
        if body.overwrite:
            if body.name not in prompt_versions.list_versions(folder):
                raise ValueError('Unknown prompt version.')
            name = prompt_versions.update_version(body.name, body.text, folder)
        else:
            name = prompt_versions.save_new_version(body.text, body.name, folder)
        return {'name': name}

    @app.post('/api/conversations/{cid}/jobs')
    def job(cid: str, body: JobInput, request: Request):
        if body.kind not in ('chat', 'summary', 'vocab', 'voca-save', 'translation-test', 'translation-download'):
            raise ValueError('Unknown task.')
        return svc(request).create_job(cid, body.kind, body.model_dump(exclude_none=True))

    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid: str, request: Request):
        svc(request).job_cancel[jid].set()
        return {'ok': True}

    @app.post('/api/jobs/{jid}/retry')
    def retry(jid: str, request: Request):
        s = svc(request)
        job = s.jobs[jid]
        if job['status'] not in ('error', 'cancelled'):
            raise ValueError('Only failed or cancelled requests can be retried.')
        return s.create_job(job['conversation_id'], job['kind'], job['_retry'])

    @app.post('/api/conversations/{cid}/translate')
    def translate(cid: str, body: TranslateInput, request: Request):
        return svc(request).translate(cid, body.ids, body.force)

    @app.post('/api/translation/stop')
    def stop_translation(request: Request):
        s = svc(request)
        with s.lock:
            s.batcher.clear()
            s.worker.cancel_all()
            s.worker.cancel_preview()
            if s.preview_timer:
                s.preview_timer.cancel()
                s.preview_timer = None
            s.preview_snapshot = None
            s.preview_revision += 1
            s.translation_preview = ''
        settings = Settings.load()
        settings.translation_enabled = False
        settings.save()
        return {'ok': True}

    @app.get('/api/translation/{line_id}/history')
    def translation_history(line_id: int, request: Request):
        return svc(request).translations.history(line_id)

    @app.get('/api/voca/collections')
    def collections():
        from caption.voca_client import VocaClient
        return VocaClient(api_key=Settings.load().voca_api_key).collections()

    @app.get('/api/conversations/{cid}/answers/{mid}/export')
    def export(cid: str, mid: int, request: Request, format: str = 'md'):
        message = next((m for m in svc(request).store.messages(cid) if m.id == mid and m.role == 'bot'), None)
        if not message:
            raise KeyError('Answer not found')
        if format not in ('md', 'docx', 'pdf'):
            raise ValueError('Choose Markdown, Word or PDF.')
        content = message.content
        rendered = False
        if message.kind == 'vocab':
            from caption.vocab import NewWord, card_html
            words = [NewWord(**word) for word in json.loads(content)]
            if format == 'md':
                content = '\n\n'.join(f'## {w.word}\n\n{w.phonetic} · {w.part_of_speech}\n\n{w.translation}\n\n{w.meaning}\n\n> {w.sentence}\n\n{w.context_translation}' for w in words)
            else:
                content = ''.join(card_html(word) for word in words)
                rendered = True
        if format == 'md':
            return Response(content, media_type='text/markdown', headers={'Content-Disposition': 'attachment; filename="answer.md"'})
        from caption.answer_export import export_html, prepare_html
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / f'answer.{format}'
            export_html(prepare_html(content, rendered=rendered), destination)
            return Response(destination.read_bytes(), media_type='application/octet-stream',
                            headers={'Content-Disposition': f'attachment; filename="answer.{format}"'})

    @app.post('/api/overlay')
    def overlay(request: Request):
        s = svc(request)
        with s.lock:
            if s.overlay_process and s.overlay_process.poll() is None:
                s.hide_overlay()
                return {'visible': False}
            s.overlay_process = subprocess.Popen([sys.executable, '-m', 'web_api.overlay', str(request.base_url)],
                                                cwd=ROOT, stdin=subprocess.PIPE,
                                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'visible': True}

    if (DIST / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/')
    def index():
        if not (DIST / 'index.html').is_file():
            return Response('Run setup_web.bat to build the React app.', status_code=503)
        return FileResponse(DIST / 'index.html')

    return app


def browser_markdown(text):
    from caption.markdown_html import _prepare_math, _FENCE, _INLINE_CODE
    # Match native handling of ChatGPT Copy's escaped delimiters, protecting code.
    prepared = _prepare_math(text)
    def inline(part):
        out, pos = [], 0
        for code in _INLINE_CODE.finditer(part):
            out.append(re.sub(r'(?<!\\)\\\((.+?)\\\)', lambda m: '$' + m[1] + '$', part[pos:code.start()], flags=re.S))
            out.append(code[0]); pos = code.end()
        out.append(re.sub(r'(?<!\\)\\\((.+?)\\\)', lambda m: '$' + m[1] + '$', part[pos:], flags=re.S))
        return ''.join(out)
    out, pos = [], 0
    for fence in _FENCE.finditer(prepared):
        out.append(inline(prepared[pos:fence.start()])); out.append(fence[0]); pos = fence.end()
    return ''.join(out) + inline(prepared[pos:])
