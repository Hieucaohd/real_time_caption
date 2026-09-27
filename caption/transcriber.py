"""Streaming speech-to-text on top of faster-whisper.

Whisper is not a streaming model, so we re-transcribe a growing audio buffer:

* Every ``step_s`` seconds the current buffer is decoded greedily and shown as a
  *partial* (grey, may still change).
* When the speaker pauses (``silence_commit_s`` of trailing silence) the buffer
  is decoded once more with beam search, emitted as *final* and cleared.
* For long speech with no pauses, all segments except the last one are
  committed once the buffer exceeds ``commit_after_s``, and everything is
  committed at ``max_buffer_s``, so latency and decode cost stay bounded.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import TARGET_RATE as SR

log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# Loaded models survive Start/Stop cycles, keyed by (model name, device preference).
_model_cache: dict[tuple[str, str], tuple[object, str]] = {}
_model_lock = threading.Lock()


@dataclass
class TranscriberConfig:
    model: str = "small.en"
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    beam_size: int = 5
    step_s: float = 0.5
    silence_commit_s: float = 0.7
    commit_after_s: float = 6.0
    max_buffer_s: float = 15.0
    min_energy: float = 0.003  # RMS below this is always treated as silence
    use_context: bool = True


@dataclass
class Event:
    kind: str  # "status" | "ready" | "partial" | "final" | "error" | "stopped"
    text: str = ""
    end_of_utterance: bool = False


def load_model(name: str, device: str, on_status: Callable[[str], None]):
    """Load (or reuse) a WhisperModel. With ``device='auto'`` try CUDA, then CPU."""
    import ctranslate2
    from faster_whisper import WhisperModel

    with _model_lock:
        cached = _model_cache.get((name, device))
        if cached:
            return cached

        candidates: list[tuple[str, str]] = []
        if device in ("auto", "cuda") and ctranslate2.get_cuda_device_count() > 0:
            candidates.append(("cuda", "float16"))
        if device in ("auto", "cpu"):
            candidates.append(("cpu", "int8"))
        if not candidates:
            raise RuntimeError("No CUDA GPU detected. Choose 'cpu' or 'auto'.")

        errors: list[str] = []
        for dev, compute_type in candidates:
            on_status(f"Loading {name} on {dev.upper()} (first run downloads the model)…")
            try:
                model = WhisperModel(
                    name, device=dev, compute_type=compute_type, download_root=str(MODELS_DIR)
                )
                # A tiny warm-up decode surfaces missing CUDA libraries now rather than mid-stream.
                segments, _ = model.transcribe(
                    np.zeros(SR // 2, dtype=np.float32), language="en", beam_size=1
                )
                list(segments)
            except Exception as exc:  # noqa: BLE001 - any backend failure means "try the next one"
                log.exception("Failed to load %s on %s", name, dev)
                errors.append(f"{dev}: {exc}")
                continue
            _model_cache[(name, device)] = (model, dev)
            return model, dev

        raise RuntimeError("Could not load model.\n" + "\n".join(errors))


class StreamingTranscriber:
    """Consumes 16 kHz audio chunks from a queue and emits caption :class:`Event` s."""

    def __init__(
        self,
        config: TranscriberConfig,
        audio_queue: "queue.Queue[np.ndarray]",
        on_event: Callable[[Event], None],
    ):
        self.cfg = config
        self._audio = audio_queue
        self._emit = on_event
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="transcriber", daemon=True)
        self._context = ""
        self._noise = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ worker

    def _run(self) -> None:
        try:
            model, dev = load_model(
                self.cfg.model, self.cfg.device, lambda msg: self._emit(Event("status", msg))
            )
        except Exception as exc:  # noqa: BLE001
            self._emit(Event("error", str(exc)))
            self._emit(Event("stopped"))
            return

        if self._stop.is_set():
            self._emit(Event("stopped"))
            return

        self._drain(timeout=0)  # drop audio that queued up while the model loaded
        self._emit(Event("ready", f"Listening — {self.cfg.model} on {dev.upper()}"))
        try:
            self._loop(model)
        except Exception as exc:  # noqa: BLE001
            log.exception("Transcriber crashed")
            self._emit(Event("error", str(exc)))
        self._emit(Event("stopped"))

    def _loop(self, model) -> None:
        cfg = self.cfg
        buf = np.zeros(0, dtype=np.float32)
        has_speech = False
        trailing_silence = 0.0
        last_audio = time.monotonic()
        last_decode = 0.0

        while not self._stop.is_set():
            chunks = self._drain(timeout=0.1)
            now = time.monotonic()
            if chunks:
                last_audio = now
                for chunk in chunks:
                    if self._is_speech(chunk):
                        has_speech = True
                        trailing_silence = 0.0
                    else:
                        trailing_silence += len(chunk) / SR
                buf = np.concatenate([buf, *chunks])

            if not has_speech:
                # Keep a short pre-roll so the first syllable of speech isn't clipped.
                if len(buf) > 2 * SR:
                    buf = buf[-SR // 2 :]
                continue

            # Loopback devices deliver no data at all while nothing is playing.
            gap = now - last_audio
            silence = trailing_silence + (gap if gap > 0.25 else 0.0)
            if silence >= cfg.silence_commit_s:
                self._commit(self._decode(model, buf, final=True), end_of_utterance=True)
                buf = np.zeros(0, dtype=np.float32)
                has_speech = False
                trailing_silence = 0.0
                continue

            if now - last_decode < cfg.step_s:
                continue
            segments = self._decode(model, buf, final=False)
            last_decode = time.monotonic()
            duration = len(buf) / SR

            if duration >= cfg.max_buffer_s:
                self._commit(segments, end_of_utterance=False)
                buf = np.zeros(0, dtype=np.float32)
                has_speech = False
                trailing_silence = 0.0
            elif duration >= cfg.commit_after_s and len(segments) >= 2:
                # Earlier segments are stable enough; keep only the last one in the buffer.
                self._commit(segments[:-1], end_of_utterance=False)
                cut = (segments[-2].end + segments[-1].start) / 2
                buf = buf[int(cut * SR) :]
                self._emit(Event("partial", segments[-1].text.strip()))
            else:
                self._emit(Event("partial", _join(segments)))

        if has_speech and len(buf) > 0.3 * SR:
            self._commit(self._decode(model, buf, final=True), end_of_utterance=True)

    # ----------------------------------------------------------------- helpers

    def _drain(self, timeout: float) -> list[np.ndarray]:
        chunks: list[np.ndarray] = []
        try:
            chunks.append(self._audio.get(timeout=timeout) if timeout else self._audio.get_nowait())
            while True:
                chunks.append(self._audio.get_nowait())
        except queue.Empty:
            pass
        return chunks

    def _is_speech(self, chunk: np.ndarray) -> bool:
        """Cheap energy gate with an adaptive noise floor; Whisper's VAD refines it later."""
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if self._noise == 0.0 or rms < self._noise:
            self._noise = rms
        else:
            self._noise += (rms - self._noise) * 0.002
        return rms > max(self.cfg.min_energy, self._noise * 3.0)

    def _decode(self, model, audio: np.ndarray, final: bool) -> list:
        prompt = self._context[-200:] if self.cfg.use_context and self._context else None
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=self.cfg.beam_size if final else 1,
            temperature=(0.0, 0.2, 0.4) if final else 0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        return [
            s
            for s in segments
            if s.text.strip() and not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)
        ]

    def _commit(self, segments: list, end_of_utterance: bool) -> None:
        text = _join(segments)
        if not any(ch.isalnum() for ch in text):
            if end_of_utterance:
                self._emit(Event("partial", ""))
            return
        self._context = (self._context + " " + text)[-500:]
        self._emit(Event("final", text, end_of_utterance=end_of_utterance))


def _join(segments: list) -> str:
    return " ".join(s.text.strip() for s in segments).strip()
