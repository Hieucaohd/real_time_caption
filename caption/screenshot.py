"""Screenshot of whatever is behind the app's own windows.

``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` (Windows 10 2004+) makes a window
invisible to screen capture while it stays visible on screen, so the capture shows
the apps underneath it. The flag is only set for the instant of the capture, so the
app can still be screenshotted or screen-shared normally the rest of the time.
"""

from __future__ import annotations

import base64
import ctypes
import io
import logging
import time
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageGrab

log = logging.getLogger(__name__)

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
MONITOR_DEFAULTTONEAREST = 2
SETTLE_S = 0.2  # DWM applies the affinity on its next composition pass
MAX_WIDTH = 1920  # keep uploads light; ChatGPT downsizes large images anyway
JPEG_QUALITY = 85

_user32 = ctypes.windll.user32


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]


class ScreenshotError(RuntimeError):
    pass


def _hwnd(window: tk.Misc) -> int | None:
    try:
        return int(window.wm_frame(), 16) if window.winfo_viewable() else None
    except (tk.TclError, ValueError):
        return None


def _monitor_bbox(hwnd: int) -> tuple[int, int, int, int]:
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ScreenshotError("Could not find the monitor the app is on.")
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def capture_behind(windows: list[tk.Misc], anchor: tk.Misc) -> Image.Image:
    """Capture the monitor ``anchor`` is on, with ``windows`` left out of the picture."""
    anchor_hwnd = _hwnd(anchor) or int(anchor.wm_frame(), 16)
    bbox = _monitor_bbox(anchor_hwnd)
    hidden = [h for h in dict.fromkeys(_hwnd(w) for w in windows) if h]
    excluded = [h for h in hidden if _user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE)]
    if len(excluded) != len(hidden):
        log.warning("Could not exclude %d window(s) from the screenshot", len(hidden) - len(excluded))
    try:
        anchor.update_idletasks()
        time.sleep(SETTLE_S)
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    finally:
        for h in excluded:
            _user32.SetWindowDisplayAffinity(h, WDA_NONE)


def thumbnail_data_uri(path: Path, width: int = 280) -> str:
    """Small JPEG preview of a saved screenshot, for showing inside the chat."""
    with Image.open(path) as image:
        image.thumbnail((width, width * 4))
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def save(image: Image.Image, folder: Path) -> Path:
    """Downscale if needed and save as JPEG; returns the file path."""
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, round(image.height * MAX_WIDTH / image.width)), Image.LANCZOS)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screen_{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg"
    image.convert("RGB").save(path, "JPEG", quality=JPEG_QUALITY)
    return path
