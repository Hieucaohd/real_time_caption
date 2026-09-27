"""Entry point: real-time English captions for Windows audio (apps or microphone)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "caption.log"


def main() -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw.exe has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    for noisy in ("faster_whisper", "httpx", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    from caption.cuda_setup import register_nvidia_dlls

    register_nvidia_dlls()

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass

    import tkinter as tk

    from caption.gui import App

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
