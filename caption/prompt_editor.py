"""Dialog for editing the summary prompt and saving the edit as a new version."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from . import prompt_versions


class PromptEditor(tk.Toplevel):
    """Opens with the text of ``base_version``; "Save as new version" never overwrites it."""

    def __init__(self, master: tk.Misc, base_version: str, on_saved: Callable[[str], None]):
        super().__init__(master)
        self._on_saved = on_saved
        self.title(f"Edit summary prompt — based on {base_version}")
        self.transient(master)
        self.minsize(560, 420)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="Placeholders filled in when sending: " + "  ".join(prompt_versions.PLACEHOLDERS),
            foreground="#666666",
        ).pack(anchor="w")

        # Bottom rows first so the expanding text box can't push them out of view.
        buttons = ttk.Frame(body, padding=(0, 8, 0, 0))
        buttons.pack(side="bottom", fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save as new version", command=self._save).pack(side="right", padx=6)

        name_row = ttk.Frame(body, padding=(0, 8, 0, 0))
        name_row.pack(side="bottom", fill="x")
        ttk.Label(name_row, text="Version name").pack(side="left")
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(name_row, textvariable=self.name_var)
        name_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.text = ScrolledText(body, wrap="word", font=("Segoe UI", 11), undo=True, height=18, width=80)
        self.text.pack(fill="both", expand=True, pady=(6, 0))
        self.text.insert("1.0", prompt_versions.read(base_version))
        self.text.edit_reset()

        self.bind("<Escape>", lambda _e: self.destroy())
        self.text.focus_set()
        self.grab_set()  # modal: the choice list can't change underneath us

    def _save(self) -> None:
        text = self.text.get("1.0", "end").strip()
        label = self.name_var.get().strip()
        if not text:
            messagebox.showwarning("Summary prompt", "The prompt is empty.", parent=self)
            return
        if not label:
            messagebox.showwarning(
                "Summary prompt", "Give this version a short name, e.g. 'shorter' or 'english'.", parent=self
            )
            return
        missing = [p for p in ("{file_name}",) if p not in text]
        if missing and not messagebox.askyesno(
            "Summary prompt",
            "The prompt doesn't mention {file_name}, so ChatGPT won't be told which attached file to read.\n\n"
            "Save anyway?",
            parent=self,
        ):
            return
        try:
            name = prompt_versions.save_new_version(text, label)
        except OSError as exc:
            messagebox.showerror("Summary prompt", f"Could not save the prompt:\n{exc}", parent=self)
            return
        self.destroy()
        self._on_saved(name)
