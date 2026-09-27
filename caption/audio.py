"""Audio capture from WASAPI loopback (sound played by apps) or a microphone.

Captured audio is down-mixed to mono, resampled to 16 kHz float32 (what Whisper
expects) and pushed onto a queue as small numpy chunks.
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass

import numpy as np
import pyaudiowpatch as pyaudio

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
CHUNK_SECONDS = 0.05


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    is_loopback: bool
    sample_rate: int
    channels: int
    is_default: bool = False

    @property
    def label(self) -> str:
        kind = "System audio" if self.is_loopback else "Microphone"
        suffix = " (default)" if self.is_default else ""
        return f"{kind}: {self.name}{suffix}"


def _to_device(info: dict, is_loopback: bool, is_default: bool) -> AudioDevice:
    name = info["name"].replace(" [Loopback]", "")
    return AudioDevice(
        index=int(info["index"]),
        name=name,
        is_loopback=is_loopback,
        sample_rate=int(info["defaultSampleRate"]),
        channels=max(1, int(info["maxInputChannels"])),
        is_default=is_default,
    )


def list_devices() -> list[AudioDevice]:
    """Return loopback devices first (default output first), then microphones."""
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)

        try:
            default_loopback = pa.get_default_wasapi_loopback()["index"]
        except (OSError, LookupError):
            default_loopback = None
        loopbacks = sorted(
            pa.get_loopback_device_info_generator(),
            key=lambda d: d["index"] != default_loopback,
        )

        default_mic = wasapi.get("defaultInputDevice", -1)
        mics = [
            d
            for d in (pa.get_device_info_by_index(i) for i in range(pa.get_device_count()))
            if d["hostApi"] == wasapi["index"]
            and d["maxInputChannels"] > 0
            and not d.get("isLoopbackDevice", False)
        ]
        mics.sort(key=lambda d: d["index"] != default_mic)

        return [_to_device(d, True, d["index"] == default_loopback) for d in loopbacks] + [
            _to_device(d, False, d["index"] == default_mic) for d in mics
        ]
    finally:
        pa.terminate()


class Resampler:
    """Streaming linear-interpolation resampler that stays continuous across chunks."""

    def __init__(self, src_rate: int, dst_rate: int):
        self._step = src_rate / dst_rate
        self._pos = 0.0
        self._carry = np.zeros(0, dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        if self._step == 1.0:
            return samples
        buf = np.concatenate([self._carry, samples])
        positions = np.arange(self._pos, len(buf) - 1, self._step)
        out = np.interp(positions, np.arange(len(buf)), buf).astype(np.float32)

        next_pos = self._pos + len(positions) * self._step
        keep_from = int(next_pos)
        self._carry = buf[keep_from:]
        self._pos = next_pos - keep_from
        return out


class AudioCapture:
    """Streams 16 kHz mono float32 chunks from ``device`` into ``out_queue``."""

    def __init__(self, device: AudioDevice, out_queue: "queue.Queue[np.ndarray]"):
        self.device = device
        self._queue = out_queue
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None
        self._resampler = Resampler(device.sample_rate, TARGET_RATE)
        self.level = 0.0  # RMS of the most recent chunk, for the UI meter

    def start(self) -> None:
        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self.device.channels,
                rate=self.device.sample_rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=int(self.device.sample_rate * CHUNK_SECONDS),
                stream_callback=self._on_audio,
            )
        except Exception:
            self._pa.terminate()
            self._pa = None
            raise
        log.info("Capturing from %s", self.device.label)

    def _on_audio(self, in_data, frame_count, time_info, status):
        samples = np.frombuffer(in_data, dtype=np.float32)
        if self.device.channels > 1:
            samples = samples.reshape(-1, self.device.channels).mean(axis=1)
        mono = self._resampler(samples)
        if mono.size:
            self.level = float(np.sqrt(np.mean(mono * mono)))
            self._queue.put(mono)
        return None, pyaudio.paContinue

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except OSError:
                log.exception("Error closing audio stream")
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
        self.level = 0.0
