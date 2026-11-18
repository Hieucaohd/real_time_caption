"""Entry point: real-time English captions for Windows audio (apps or microphone)."""

from __future__ import annotations

import logging
import os
import sys

from caption import paths


def main() -> None:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(paths.LOG_PATH, encoding="utf-8")]
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
    if paths.FROZEN:
        # PyInstaller points matplotlib at a fresh temp dir each run, rebuilding its font cache.
        os.environ["MPLCONFIGDIR"] = str(paths.CACHE_DIR / "matplotlib")
    logging.getLogger(__name__).info("Data folder: %s (packaged: %s)", paths.DATA_DIR, paths.FROZEN)
    paths.install_default_prompts()

    from caption.cuda_setup import register_nvidia_dlls

    register_nvidia_dlls()

    if "--self-test" in sys.argv:  # used to verify packaged builds, see caption/selftest.py
        from caption import selftest

        sys.exit(selftest.run(sys.argv))

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass

    import tkinter as tk

    from caption.gui import App

    root = tk.Tk()
    if paths.ICON_PATH.exists():
        root.iconbitmap(default=str(paths.ICON_PATH))  # also used by dialogs and the overlay
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
