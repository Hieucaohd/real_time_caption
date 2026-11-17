"""Push persisted Google snapshots to local web clients without polling."""
import asyncio
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from anyio import CancelScope

from caption.settings import Settings
from caption.translation import Config

router = APIRouter()


class TranslationUpdates:
    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = set()

    def subscribe(self, callback):
        with self.lock:
            self.listeners.add(callback)

    def unsubscribe(self, callback):
        with self.lock:
            self.listeners.discard(callback)

    def publish(self, cid, config):
        with self.lock:
            callbacks = tuple(self.listeners)
        for callback in callbacks:
            callback(cid, config)


@router.websocket('/api/conversations/{cid}/translation-stream')
async def translation_stream(websocket: WebSocket, cid: str):
    origin = urlparse(websocket.headers.get('origin', ''))
    if (websocket.url.hostname not in ('127.0.0.1', 'localhost', 'testserver')
            or origin.scheme not in ('http', 'https') or origin.netloc != websocket.url.netloc):
        await websocket.close(code=1008)
        return
    service = websocket.app.state.service
    if not service.store.get(cid):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    loop = asyncio.get_running_loop()
    pending = asyncio.Queue(maxsize=1)
    live = True

    def enqueue():
        if live and pending.empty():
            pending.put_nowait(True)

    def changed(conversation_id, config):
        if live and conversation_id == cid:
            try:
                loop.call_soon_threadsafe(enqueue)
            except RuntimeError:
                pass  # Client/server event loop has already closed.

    async def send():
        while True:
            await pending.get()
            config = Config.from_settings(Settings.load())
            reading = await asyncio.to_thread(service.translations.reading, cid, config)
            await websocket.send_json({'conversation_id': cid, 'engine': config.engine,
                'source': config.source, 'target': config.target, 'reading': reading})

    async def receive():
        while True:
            await websocket.receive_text()  # Detect disconnect even when there are no translations.

    service.translation_updates.subscribe(changed)
    enqueue()  # Reconnect always gets the latest database snapshot.
    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        live = False
        service.translation_updates.unsubscribe(changed)
        for task in tasks:
            task.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
