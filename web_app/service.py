from __future__ import annotations

import json
import queue
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

import numpy as np

from caption import chatgpt
from caption.transcriber import Event, StreamingTranscriber, TranscriberConfig
from .store import DATA_DIR, Store, Transcript

SETTINGS_PATH = DATA_DIR / "settings.json"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class WebSettings:
    token: str = ""
    model: str = "small.en"
    compute: str = "auto"
    chat_power: int = -1
    summary_power: int = -1
    chat_max_lines: int = 300
    summary_max_lines: int = 300

    @classmethod
    def load(cls):
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        known = {f.name for f in fields(cls)}
        value = cls(**{k: v for k, v in data.items() if k in known})
        if not value.token:
            value.token = secrets.token_urlsafe(24)
            value.save()
        return value

    def save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


class Resampler:
    def __init__(self, source_rate: int, target_rate: int = 16_000):
        self.step, self.pos = source_rate / target_rate, 0.0
        self.carry = np.zeros(0, dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        if self.step == 1:
            return samples
        buf = np.concatenate((self.carry, samples))
        positions = np.arange(self.pos, len(buf) - 1, self.step)
        out = np.interp(positions, np.arange(len(buf)), buf).astype(np.float32)
        next_pos = self.pos + len(positions) * self.step
        keep = int(next_pos)
        self.carry, self.pos = buf[keep:], next_pos - keep
        return out


class Service:
    def __init__(self):
        self.store, self.settings = Store(), WebSettings.load()
        self.lock = threading.RLock()
        self.chat_lock = threading.Lock()
        self.capture = None
        self.tasks: dict[str, dict] = {}

    def start_capture(self, cid: str | None, title: str, sample_rate: int) -> dict:
        with self.lock:
            if self.capture:
                raise ValueError("Another web microphone is already active")
            item = self.store.get(cid) if cid else self.store.create(title or "iPhone microphone")
            if not item:
                raise ValueError("Conversation not found")
            transcript, audio = Transcript(item, self.store), queue.Queue()
            transcript.begin()
            state = {"conversation_id": item.id, "status": "Loading Whisper…", "partial": "",
                     "transcript": transcript, "audio": audio, "resampler": Resampler(sample_rate)}
            self.capture = state
            self.store.status(item.id, "recording")

            def event(value: Event):
                with self.lock:
                    if value.kind in ("status", "ready", "error"):
                        state["status"] = value.text or value.kind
                    elif value.kind == "partial":
                        state["partial"] = value.text
                    elif value.kind == "final":
                        transcript.write(value.text, value.end_of_utterance)
                        state["partial"] = ""
                    elif value.kind == "stopped":
                        self.store.status(item.id, "paused")
                        state["status"] = "Paused"
                        if self.capture is state:
                            self.capture = None

            transcriber = StreamingTranscriber(
                TranscriberConfig(model=self.settings.model, device=self.settings.compute), audio, event
            )
            state["transcriber"] = transcriber
            transcriber.start()
            return item.json()

    def audio(self, cid: str, data: bytes) -> None:
        with self.lock:
            state = self.capture
            if not state or state["conversation_id"] != cid:
                raise ValueError("This microphone session is not active")
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            chunk = state["resampler"](samples)
            if len(chunk):
                state["audio"].put(chunk)

    def stop_capture(self) -> None:
        with self.lock:
            if self.capture:
                self.capture["status"] = "Stopping…"
                self.capture["transcriber"].stop()

    def state(self) -> dict:
        with self.lock:
            if not self.capture:
                return {"active": False, "status": "Idle", "partial": ""}
            return {"active": True, "conversation_id": self.capture["conversation_id"],
                    "status": self.capture["status"], "partial": self.capture["partial"]}

    def detail(self, cid: str) -> dict:
        item = self.store.get(cid)
        if not item:
            raise ValueError("Conversation not found")
        return {"conversation": item.json(), "transcript": Transcript(item, self.store).read(),
                "messages": self.store.messages(cid), "capture": self.state()}

    def submit(self, kind: str, cid: str, message: str = "", *, new_chat: bool = False,
               attach_transcript: bool = True, attach_history: bool = False) -> str:
        item = self.store.get(cid)
        if not item:
            raise ValueError("Conversation not found")
        task_id = uuid.uuid4().hex
        self.tasks[task_id] = {"id": task_id, "status": "queued", "result": "", "error": ""}

        def work():
            task = self.tasks[task_id]
            try:
                task["status"] = "running"
                transcript = Transcript(item, self.store)
                if kind == "summary" and not transcript.read():
                    raise ValueError("Conversation has no transcript yet")
                if kind == "summary":
                    upload = transcript.snapshot(self.settings.summary_max_lines)
                    prompt = chatgpt.build_prompt(upload, item.source, datetime.fromisoformat(item.created_at),
                                                  PROMPT_DIR / "summary.txt")
                    attachments, power = upload, self.settings.summary_power
                    self.store.add_message(cid, "user", "summary", "Summarize this conversation")
                else:
                    upload = transcript.snapshot(self.settings.chat_max_lines) if attach_transcript else None
                    prompt = chatgpt.build_prompt(upload, item.source if upload else "",
                                                  datetime.fromisoformat(item.created_at) if upload else None,
                                                  PROMPT_DIR / "chat.txt", message)
                    attachments = [p for p in (upload, self._history(cid) if attach_history else None) if p]
                    power = self.settings.chat_power
                    self.store.add_message(cid, "user", "chat", message)
                with self.chat_lock:
                    answer = chatgpt.ask(prompt, attachments or None, lambda s: task.update(status=s),
                                         new_chat=new_chat or kind == "summary",
                                         tab_name=f"{chatgpt.APP_TAB}-web-{cid}",
                                         power=power if power >= 0 else None)
                self.store.add_message(cid, "bot", kind, answer)
                if kind == "summary":
                    with (item.folder / "summaries.md").open("a", encoding="utf-8") as f:
                        f.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S}\n\n{answer}\n\n---\n\n")
                task.update(status="done", result=answer)
            except Exception as exc:
                task.update(status="error", error=str(exc))

        threading.Thread(target=work, name=f"web-{kind}", daemon=True).start()
        return task_id

    def _history(self, cid: str) -> Path | None:
        messages = self.store.messages(cid)
        if not messages:
            return None
        item = self.store.get(cid)
        path = item.folder / ".web-chat-history.md"
        path.write_text("\n\n".join(f"## {m['role']}\n{m['content']}" for m in messages), encoding="utf-8")
        return path
