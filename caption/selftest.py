"""``RealTimeCaption.exe --self-test``: check that a packaged build has everything it needs.

A windowed .exe has no console, so results are written to ``selftest.txt`` in the data
folder (and printed when a console exists). Exit code 0 = all checks passed. Pass
``--wav <file> --model <name>`` to also transcribe a real recording (on CUDA when the build
has the NVIDIA libraries and the PC has a GPU, otherwise on the CPU).
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Callable

from . import paths


def _checks(wav: Path | None, model: str) -> list[tuple[str, Callable[[], str]]]:
    def whisper() -> str:
        import ctranslate2
        import faster_whisper

        info = f"ctranslate2 {ctranslate2.__version__}, faster-whisper {faster_whisper.__version__}"
        if wav is None:
            return info + " (no --wav given, transcription skipped)"
        from .transcriber import load_model

        m, dev = load_model(model, "auto", lambda _msg: None)  # GPU builds use CUDA here
        segments, _ = m.transcribe(str(wav), language="en", vad_filter=True)
        return f"{info}; {model} on {dev}: " + " ".join(s.text.strip() for s in segments)[:120]

    def audio() -> str:
        from .audio import list_devices

        devices = list_devices()
        return f"{len(devices)} devices, {sum(d.is_loopback for d in devices)} loopback"

    def cuda() -> str:
        from .cuda_setup import cuda_runtime_available, register_nvidia_dlls

        folders = register_nvidia_dlls()
        return f"CUDA libraries {'available' if cuda_runtime_available() else 'not bundled (CPU build)'}; {len(folders)} folders"

    def ui_automation() -> str:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            return f"desktop has {len(auto.GetRootControl().GetChildren())} top-level windows"

    def playwright() -> str:
        from playwright._impl._driver import compute_driver_executable

        driver = compute_driver_executable()
        exe = Path(driver[0] if isinstance(driver, tuple) else driver)
        if not exe.exists():
            raise FileNotFoundError(exe)
        return f"driver at {exe}"

    def chrome() -> str:
        """Connect to Chrome over CDP if it is running (read-only: counts ChatGPT tabs)."""
        import socket

        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", 9222)) != 0:
                return "SKIPPED (Chrome is not running on port 9222)"
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://localhost:9222", timeout=10_000)
            tabs = [pg for ctx in browser.contexts for pg in ctx.pages if "chatgpt.com" in pg.url]
            return f"connected over CDP, {len(tabs)} ChatGPT tab(s)"

    def math() -> str:
        from . import mathjax_render

        out = mathjax_render.display_html(
            r"\begin{array}{c|c}f(x)&\int_0^b f(x)\,dx\\\hline x^2&\frac{b^3}{3}\end{array}"
        )
        if "data:image/png" not in out:
            raise RuntimeError("formula was not rendered")
        return "offline MathJax renders formulas"

    def html_view() -> str:
        import tkinter as tk

        from . import markdown_html

        root = tk.Tk()
        root.withdraw()
        try:
            view = markdown_html.make_view(root)
            markdown_html.show(view, markdown_html.to_html("| a | b |\n|---|---|\n| 1 | 2 |"))
            root.update()
        finally:
            root.destroy()
        return "tkinterweb/Tkhtml loaded"

    def screenshot() -> str:
        from PIL import ImageGrab

        img = ImageGrab.grab(all_screens=True)
        return f"captured {img.width}x{img.height}"

    def prompts() -> str:
        paths.install_default_prompts()
        found = sorted(p.relative_to(paths.PROMPTS_DIR).as_posix() for p in paths.PROMPTS_DIR.rglob("*.txt"))
        if not found:
            raise FileNotFoundError(paths.PROMPTS_DIR)
        return ", ".join(found)

    return [
        ("prompts", prompts), ("cuda", cuda), ("whisper", whisper), ("audio devices", audio),
        ("ui automation", ui_automation), ("playwright", playwright), ("chrome", chrome), ("math", math),
        ("html view", html_view), ("screenshot", screenshot),
    ]


def run(argv: list[str]) -> int:
    wav = Path(argv[argv.index("--wav") + 1]) if "--wav" in argv else None
    model = argv[argv.index("--model") + 1] if "--model" in argv else "tiny.en"
    lines = [f"data folder: {paths.DATA_DIR} (packaged: {paths.FROZEN})"]
    failed = 0
    for name, check in _checks(wav, model):
        start = time.monotonic()
        try:
            detail = check()
            lines.append(f"PASS {name:14} {detail}  ({time.monotonic() - start:.1f}s)")
        except Exception:  # noqa: BLE001 - report every failure, keep going
            failed += 1
            lines.append(f"FAIL {name:14}\n" + traceback.format_exc())
    lines.append("ALL PASSED" if not failed else f"{failed} CHECK(S) FAILED")
    report = "\n".join(lines)
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / "selftest.txt").write_text(report + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(report)
    return 1 if failed else 0
