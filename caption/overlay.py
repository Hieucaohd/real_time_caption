"""Floating caption overlay: borderless, always on top, optionally see-through.

Modes:
* **Box**: text on a semi-transparent dark bar.
* **Transparent**: only the text is visible (with a dark outline so it stays
  readable on any background). Uses a colour key, so the empty areas are also
  click-through.
* **Click-through** (either mode): the whole overlay ignores the mouse, so it can
  sit over a video or game without getting in the way. Toggle it from the main
  window, because the overlay itself can no longer be clicked.

The overlay never takes keyboard focus and re-asserts "topmost" periodically, so
clicking into other apps doesn't bury it.
"""

from __future__ import annotations

import ctypes
import logging
import time
import tkinter as tk
from tkinter import font as tkfont

from .settings import Settings

log = logging.getLogger(__name__)

BOX_BG = "#101010"
KEY_COLOR = "#010203"  # painted pixels of this exact colour become fully transparent
TEXT_COLOR = "#ffffff"
PARTIAL_COLOR = "#c8c8c8"
OUTLINE_COLOR = "#000000"
PAD_X, PAD_Y = 18, 8
CLEAR_AFTER_S = 8.0
TOPMOST_EVERY_S = 1.5

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

try:
    _user32 = ctypes.windll.user32
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    _user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
except AttributeError:  # not on Windows
    _user32 = None


class CaptionOverlay:
    """Drag to move, wheel = font size, Ctrl+wheel = width, right-click for options."""

    def __init__(self, master: tk.Misc, settings: Settings):
        self.settings = settings
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.title("Caption overlay")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        # A WM_CLOSE (e.g. from another program) would otherwise destroy the overlay for good.
        self.win.protocol("WM_DELETE_WINDOW", lambda: self.hide())

        self.font = tkfont.Font(family="Segoe UI", size=settings.overlay_font_size, weight="bold")
        self.canvas = tk.Canvas(self.win, bd=0, highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)

        self._committed = ""
        self._partial = ""
        self._last_update = time.monotonic()
        self._last_topmost = 0.0
        self._drag_offset = (0, 0)
        self._width = 0

        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: self._remember_position())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-3>", self._show_menu)

        self.transparent_var = tk.BooleanVar(value=settings.overlay_transparent)
        self.menu = tk.Menu(self.win, tearoff=False)
        self.menu.add_checkbutton(
            label="Transparent (text only)",
            variable=self.transparent_var,
            command=lambda: self.set_transparent(self.transparent_var.get()),
        )
        self.menu.add_command(label="Click-through (unlock from main window)", command=lambda: self.on_click_through(True))
        self.menu.add_separator()
        self.menu.add_command(label="Bigger text", command=lambda: self._change_font(2))
        self.menu.add_command(label="Smaller text", command=lambda: self._change_font(-2))
        self.menu.add_command(label="Wider", command=lambda: self._change_width(80))
        self.menu.add_command(label="Narrower", command=lambda: self._change_width(-80))
        self.menu.add_separator()
        for n in (1, 2, 3, 4):
            self.menu.add_command(label=f"{n} line{'s' if n > 1 else ''}", command=lambda n=n: self._set_lines(n))
        self.menu.add_separator()
        self.menu.add_command(label="Hide overlay", command=self.hide)

        # Callbacks the main window hooks into to keep its checkboxes in sync.
        self.on_hidden = lambda: None
        self.on_click_through = lambda enabled: self.set_click_through(enabled)

        self._apply_mode()
        self._apply_geometry()

    # ------------------------------------------------------------ public API

    def show(self) -> None:
        self.win.deiconify()
        self.win.update_idletasks()
        self._apply_window_styles()
        self._render()

    def hide(self) -> None:
        self.win.withdraw()
        self.on_hidden()

    def set_transparent(self, enabled: bool) -> None:
        self.settings.overlay_transparent = enabled
        self.transparent_var.set(enabled)
        self._apply_mode()
        self._render()

    def set_click_through(self, enabled: bool) -> None:
        self.settings.overlay_click_through = enabled
        self._apply_window_styles()

    def set_partial(self, text: str) -> None:
        self._partial = text
        self._last_update = time.monotonic()
        self._render()

    def add_final(self, text: str) -> None:
        self._committed = (self._committed + " " + text).strip()[-800:]
        self._partial = ""
        self._last_update = time.monotonic()
        self._render()

    def clear(self) -> None:
        self._committed = ""
        self._partial = ""
        self._render()

    def tick(self) -> None:
        """Called frequently by the app: clear stale captions and stay on top."""
        now = time.monotonic()
        if (self._committed or self._partial) and now - self._last_update > CLEAR_AFTER_S:
            self.clear()
        if now - self._last_topmost > TOPMOST_EVERY_S:
            self._last_topmost = now
            self._raise_topmost()

    # ------------------------------------------------------------- rendering

    def _apply_mode(self) -> None:
        if self.settings.overlay_transparent:
            bg = KEY_COLOR
            self.win.attributes("-alpha", 1.0)
            self.win.attributes("-transparentcolor", KEY_COLOR)
        else:
            bg = BOX_BG
            self.win.attributes("-transparentcolor", "")
            self.win.attributes("-alpha", self.settings.overlay_alpha)
        self.win.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self._apply_window_styles()  # Tk may rewrite the extended style when attributes change

    def _wrap(self, max_width: int) -> list[list[tuple[str, bool]]]:
        """Greedy word wrap of committed + partial text; returns the last N lines."""
        words = [(w, False) for w in self._committed.split()] + [(w, True) for w in self._partial.split()]
        space = self.font.measure(" ")
        lines: list[list[tuple[str, bool]]] = []
        line: list[tuple[str, bool]] = []
        line_width = 0
        # Only the tail can be visible; skip measuring text that would scroll off anyway.
        for word, partial in words[-60 * self.settings.overlay_lines :]:
            w = self.font.measure(word)
            if line and line_width + space + w > max_width:
                lines.append(line)
                line, line_width = [], 0
            line_width += w if not line else space + w
            line.append((word, partial))
        if line:
            lines.append(line)
        return lines[-self.settings.overlay_lines :]

    def _render(self) -> None:
        c = self.canvas
        c.delete("all")
        width = self._width
        linespace = self.font.metrics("linespace")
        transparent = self.settings.overlay_transparent
        outline = max(1, self.settings.overlay_font_size // 11) if transparent else 0

        for row, line in enumerate(self._wrap(width - 2 * PAD_X)):
            # Group consecutive words of the same kind into coloured runs, then centre the line.
            runs: list[tuple[str, bool]] = []
            for word, partial in line:
                if runs and runs[-1][1] == partial:
                    runs[-1] = (runs[-1][0] + " " + word, partial)
                else:
                    runs.append((word, partial))
            texts = [text + (" " if i < len(runs) - 1 else "") for i, (text, _) in enumerate(runs)]
            x = (width - sum(self.font.measure(t) for t in texts)) // 2
            y = PAD_Y + row * linespace
            for text, (_, partial) in zip(texts, runs):
                if outline:
                    for dx in (-outline, 0, outline):
                        for dy in (-outline, 0, outline):
                            if dx or dy:
                                c.create_text(x + dx, y + dy, text=text, font=self.font, fill=OUTLINE_COLOR, anchor="nw")
                c.create_text(
                    x, y, text=text, font=self.font, anchor="nw",
                    fill=PARTIAL_COLOR if partial else TEXT_COLOR,
                )
                x += self.font.measure(text)

    # ------------------------------------------------------------ win32 glue

    def _hwnd(self) -> int | None:
        if _user32 is None:
            return None
        try:
            return int(self.win.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return None

    def _apply_window_styles(self) -> None:
        """No focus stealing, hidden from Alt+Tab, optional click-through."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if self.settings.overlay_click_through:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
        self._raise_topmost()

    def _raise_topmost(self) -> None:
        hwnd = self._hwnd()
        if hwnd and self.win.winfo_viewable():
            _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # --------------------------------------------------------------- geometry

    def _apply_geometry(self) -> None:
        s = self.settings
        screen_w, screen_h = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        width = s.overlay_width or int(screen_w * 0.6)
        height = s.overlay_lines * self.font.metrics("linespace") + 2 * PAD_Y + 4
        x = s.overlay_x if s.overlay_x >= 0 else (screen_w - width) // 2
        y = s.overlay_y if s.overlay_y >= 0 else screen_h - height - int(screen_h * 0.1)
        # Keep the overlay reachable if the monitor layout changed.
        x = min(max(0, x), max(0, screen_w - 100))
        y = min(max(0, y), max(0, screen_h - 40))
        self._width = width
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self._render()

    def _start_drag(self, event: tk.Event) -> None:
        self._drag_offset = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _drag(self, event: tk.Event) -> None:
        dx, dy = self._drag_offset
        self.win.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _remember_position(self) -> None:
        self.settings.overlay_x = self.win.winfo_x()
        self.settings.overlay_y = self.win.winfo_y()

    def _on_wheel(self, event: tk.Event) -> None:
        step = 1 if event.delta > 0 else -1
        if event.state & 0x0004:  # Ctrl held
            self._change_width(step * 40)
        else:
            self._change_font(step)

    def _change_font(self, delta: int) -> None:
        self.settings.overlay_font_size = min(72, max(10, self.settings.overlay_font_size + delta))
        self.font.configure(size=self.settings.overlay_font_size)
        self._remember_position()
        self._apply_geometry()

    def _change_width(self, delta: int) -> None:
        self.settings.overlay_width = max(300, self._width + delta)
        self._remember_position()
        self._apply_geometry()

    def _set_lines(self, n: int) -> None:
        self.settings.overlay_lines = n
        self._remember_position()
        self._apply_geometry()

    def _show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)
