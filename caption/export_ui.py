"""Per-answer Export links and the native format/save dialogs."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import answer_export

log = logging.getLogger(__name__)


def actions(key: str) -> str:
    return f"<div class='message-actions'><a href='rtc-answer-export:{key}'>Export…</a></div>"


def choose_export(parent, content: str, filename: str, *, rendered: bool = False) -> None:
    """Must run after_idle, not inside Tkhtml's link event callback."""
    dialog = tk.Toplevel(parent)
    dialog.title("Export ChatGPT response")
    dialog.transient(parent.winfo_toplevel())
    dialog.resizable(False, False)
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Choose the file format").pack(anchor="w", pady=(0, 12))

    def save(extension):
        dialog.destroy()
        destination = filedialog.asksaveasfilename(
            parent=parent, title="Export ChatGPT response", initialfile=filename + extension,
            defaultextension=extension,
            filetypes=[("Word document" if extension == ".docx" else "PDF document", "*" + extension)],
        )
        if not destination:
            return
        if Path(destination).suffix.lower() != extension:
            messagebox.showerror("Export", f"Please save using the {extension} extension.", parent=parent)
            return
        try:
            # Render MathJax on its owning UI thread before the document worker.
            prepared = answer_export.prepare_html(content, rendered=rendered)
        except Exception as exc:
            log.exception("Could not prepare answer export")
            messagebox.showerror("Export", str(exc), parent=parent)
            return
        _start_export(parent, prepared, Path(destination))

    row = ttk.Frame(frame)
    row.pack(fill="x")
    ttk.Button(row, text="Word (.docx)", command=lambda: save(".docx")).pack(side="left", padx=(0, 8))
    ttk.Button(row, text="PDF (.pdf)", command=lambda: save(".pdf")).pack(side="left")
    ttk.Button(frame, text="Cancel", command=dialog.destroy).pack(anchor="e", pady=(12, 0))
    dialog.grab_set()


def _start_export(parent, content: str, destination: Path) -> None:
    progress = tk.Toplevel(parent)
    progress.title("Exporting response")
    progress.transient(parent.winfo_toplevel())
    ttk.Label(progress, text="Creating " + destination.name, padding=16).pack()
    bar = ttk.Progressbar(progress, mode="indeterminate", length=280)
    bar.pack(padx=16, pady=(0, 16))
    bar.start()
    results = queue.Queue()

    def worker():
        try:
            answer_export.export_html(content, destination)
        except Exception as exc:
            log.exception("Answer export failed")
            results.put(str(exc))
        else:
            results.put(None)

    def poll():
        try:
            error = results.get_nowait()
        except queue.Empty:
            try:
                parent.after(100, poll)
            except tk.TclError:
                pass
            return
        try:
            if progress.winfo_exists():
                bar.stop()
                progress.destroy()
            if error:
                messagebox.showerror("Export failed", error, parent=parent)
            else:
                messagebox.showinfo("Export", f"Saved to:\n{destination}", parent=parent)
        except tk.TclError:
            pass  # The app may have closed while the export was finishing.

    threading.Thread(target=worker, name="answer-export", daemon=True).start()
    parent.after(100, poll)
