"""Conversation-scoped bilingual translation with independently configured engines."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import markdown_html, translation
from .translation_store import TranslationStore
from .translation_worker import TranslationWorker

FIELDS = {
    "azure": [("translation_azure_key", "API key", True), ("translation_azure_region", "Region (if required)", False)],
    "google": [("translation_google_key", "API key (Basic v2)", True)],
    "google_web": [],
    "gemini": [("translation_gemini_key", "API key", True), ("translation_gemini_model", "Model ID", False)],
    "vinai": [],
}
NOTES = {
    "azure": "Cloud: sends transcript text to Microsoft. Configure a Translator resource/key and region. Quota depends on your plan.",
    "google": "Cloud: sends text to Google Cloud Translation Basic v2. Enable the API in the project that owns this key. Billing/quota depends on the project.",
    "google_web": "No API key. Open Google Translate in Chrome launched with launch_chrome.bat (port 9222). Reuses one existing Translate tab, types the source text and reads Copy translation. Text is sent to Google; the tab's previous input is replaced. Other tabs and your system clipboard are left alone.",
    "gemini": "Cloud: sends text to Gemini API, not the Chrome tab. Free-tier data may be used to improve Google's products. Check your API quota.",
    "vinai": "Offline: English → Vietnamese only. CPU is the safe default. CUDA shares GPU resources with Whisper. Model: VinAI Translate (AGPL-3.0). Download is explicit, never automatic.",
}


class TranslationPanel(ttk.Frame):
    def __init__(self, master, settings, store, get_session_file, get_conversation_id):
        super().__init__(master)
        self.settings = settings
        self._get_session_file = get_session_file
        self._get_conversation_id = get_conversation_id
        self.store = TranslationStore(store.db_path)
        self.store.recover()
        self.worker = TranslationWorker(self.store)
        self._results = queue.Queue()
        self._lines = {}
        self._revision = 0
        self._preview_after = None
        self._preview_snapshot = None
        self._closed = False
        self._utility_busy = False
        self._fields = {name: tk.StringVar(value=getattr(settings, name)) for fields in FIELDS.values() for name, _, _ in fields}
        engine = settings.translation_engine if settings.translation_engine in translation.ENGINES else "azure"
        self.engine_var = tk.StringVar(value=translation.ENGINES[engine])
        self.source_var = tk.StringVar(value=settings.translation_source)
        self.target_var = tk.StringVar(value=settings.translation_target)
        self.device_var = tk.StringVar(value=settings.translation_device)
        self.enabled_var = tk.BooleanVar(value=settings.translation_enabled)
        self.partial_var = tk.BooleanVar(value=settings.translation_partial)
        self.follow_var = tk.BooleanVar(value=True)

        bar = ttk.Frame(self, padding=(4, 5))
        bar.pack(fill="x")
        ttk.Label(bar, text="Engine").pack(side="left", padx=(0, 4))
        engine_box = ttk.Combobox(bar, textvariable=self.engine_var, values=list(translation.ENGINES.values()), state="readonly", width=24)
        engine_box.pack(side="left")
        engine_box.bind("<<ComboboxSelected>>", lambda _e: self._engine_changed())
        ttk.Button(bar, text="Settings", command=self.toggle_settings).pack(side="left", padx=5)
        ttk.Checkbutton(bar, text="Auto translate", variable=self.enabled_var, command=self._toggle_enabled).pack(side="left", padx=4)
        options = ttk.Frame(self, padding=(4, 0))
        options.pack(fill="x")
        ttk.Checkbutton(options, text="Translate provisional speech (extra requests)", variable=self.partial_var, command=self._options_changed).pack(side="left")
        ttk.Checkbutton(options, text="Follow latest", variable=self.follow_var).pack(side="left", padx=8)
        self.status = tk.StringVar(value="Translation is off. Configure an engine, then enable Auto translate.")
        self.status_label = ttk.Label(self, textvariable=self.status, wraplength=650, foreground="#666666", padding=4)
        self.status_label.pack(fill="x")

        self.config_frame = ttk.LabelFrame(self, text="Translation settings", padding=8)
        langs = ttk.Frame(self.config_frame)
        langs.pack(fill="x")
        for label, var in (("From", self.source_var), ("To", self.target_var)):
            ttk.Label(langs, text=label).pack(side="left", padx=(0, 4))
            box = ttk.Combobox(langs, textvariable=var, values=list(translation.LANGUAGES), width=6, state="readonly")
            box.pack(side="left", padx=(0, 12))
            box.bind("<<ComboboxSelected>>", lambda _e: self._options_changed())
        self._config_pages = {}
        for key, fields in FIELDS.items():
            page = ttk.Frame(self.config_frame)
            self._config_pages[key] = page
            for name, label, secret in fields:
                row = ttk.Frame(page)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=label, width=22).pack(side="left")
                ttk.Entry(row, textvariable=self._fields[name], show="*" if secret else "").pack(side="left", fill="x", expand=True)
            if key == "gemini":
                ttk.Label(page, text="Prompt ({source}, {target}, {text})").pack(anchor="w")
                self.prompt = tk.Text(page, height=3, wrap="word", font=("Segoe UI", 10))
                self.prompt.insert("1.0", settings.translation_gemini_prompt)
                self.prompt.pack(fill="x")
            if key == "vinai":
                row = ttk.Frame(page)
                row.pack(fill="x", pady=3)
                ttk.Label(row, text="Run on").pack(side="left", padx=(0, 8))
                ttk.Combobox(row, textvariable=self.device_var, values=["cpu", "cuda", "auto"], state="readonly", width=8).pack(side="left")
                self.download_btn = ttk.Button(row, text="Download model…", command=self.download_model)
                self.download_btn.pack(side="left", padx=8)
            ttk.Label(page, text=NOTES[key], wraplength=650, foreground="#666666").pack(fill="x", pady=4)
        controls = ttk.Frame(self.config_frame)
        controls.pack(side="bottom", fill="x", pady=(6, 0))
        self.test_btn = ttk.Button(controls, text="Test connection / model", command=self.test_connection)
        self.test_btn.pack(side="left")
        ttk.Button(controls, text="Save settings", command=self.save_settings).pack(side="left", padx=8)
        ttk.Label(controls, text="Keys are stored locally in settings.json", foreground="#777777").pack(side="left")

        actions = ttk.Frame(self, padding=4)
        self.actions_frame = actions
        actions.pack(fill="x")
        ttk.Button(actions, text="Translate selected / Retry", command=self.translate_selected).pack(side="left")
        ttk.Button(actions, text="Translate missing…", command=self.translate_missing).pack(side="left", padx=6)
        ttk.Button(actions, text="Stop", command=self.stop_translation).pack(side="left")
        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True)
        list_frame = ttk.Frame(body)
        self.tree = ttk.Treeview(list_frame, columns=("stamp", "status"), selectmode="browse", height=7)
        self.tree.heading("#0", text="Transcript line")
        self.tree.heading("stamp", text="Time")
        self.tree.heading("status", text="Last translation")
        self.tree.column("#0", width=380)
        self.tree.column("stamp", width=150, stretch=False)
        self.tree.column("status", width=155, stretch=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        body.add(list_frame, weight=1)
        self.view = markdown_html.make_view(body)
        body.add(self.view, weight=2)
        self.preview_label = ttk.Label(self, text="", wraplength=650, foreground="#777777", padding=4)
        self.preview_label.pack(fill="x")
        self._engine_changed()
        self.after(100, self._drain)

    def engine(self):
        return next((key for key, label in translation.ENGINES.items() if label == self.engine_var.get()), "azure")

    def persist_settings(self):
        self.settings.translation_engine = self.engine()
        self.settings.translation_source, self.settings.translation_target = self.source_var.get(), self.target_var.get()
        self.settings.translation_enabled, self.settings.translation_partial = self.enabled_var.get(), self.partial_var.get()
        self.settings.translation_device = self.device_var.get()
        self.settings.translation_gemini_prompt = self.prompt.get("1.0", "end-1c")
        for name, variable in self._fields.items():
            setattr(self.settings, name, variable.get().strip())

    def config(self):
        self.persist_settings()
        config = translation.Config.from_settings(self.settings)
        config.validate()
        if config.engine == "vinai":
            translation.check_offline_ready()
        return config

    def save_settings(self):
        self.persist_settings()
        self.settings.save()
        self.status.set("Translation settings saved locally.")

    def toggle_settings(self):
        if self.config_frame.winfo_manager():
            self.config_frame.pack_forget()
        else:
            self.config_frame.pack(fill="x", before=self.actions_frame)

    def _engine_changed(self):
        self.invalidate_preview()
        for page in self._config_pages.values():
            page.pack_forget()
        self._config_pages[self.engine()].pack(fill="x")
        try:
            self.config()
        except translation.TranslationError as exc:
            self.enabled_var.set(False)
            self.status.set(str(exc))
        else:
            self.status.set(f"Selected {self.engine_var.get()}. Existing queued requests keep their original engine.")
        self.persist_settings()

    def _options_changed(self):
        self.invalidate_preview()
        self.persist_settings()
        if self.enabled_var.get():
            try:
                self.config()
            except translation.TranslationError as exc:
                self.enabled_var.set(False)
                self.settings.translation_enabled = False
                self.status.set(str(exc))

    def _toggle_enabled(self):
        if self.enabled_var.get():
            try:
                self.config()
                self.worker.resume()
            except translation.TranslationError as exc:
                self.enabled_var.set(False)
                messagebox.showinfo("Translation", str(exc), parent=self)
        else:
            self.stop_translation()
        self.persist_settings()

    def invalidate_preview(self):
        self._revision += 1
        self._preview_snapshot = None
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        self.worker.cancel_preview()
        self.preview_label.configure(text="")

    def on_partial(self, transcript, text):
        if not text.strip() or not self.enabled_var.get() or not self.partial_var.get():
            self.invalidate_preview()
            return
        if transcript.conversation_id != self._get_conversation_id():
            return
        revision = self._revision
        combined = " ".join(part for part in (transcript.pending_text, text.strip()) if part)
        self._preview_snapshot = (transcript.conversation_id, combined)
        if self._preview_after is not None:
            return

        def submit():
            self._preview_after = None
            if revision != self._revision or self._closed or not self._preview_snapshot:
                return
            try:
                cid, latest = self._preview_snapshot
                self.worker.preview(cid, latest, self.config(), revision)
                self.preview_label.configure(text="Provisional: translating…")
            except translation.TranslationError as exc:
                self.status.set(str(exc))

        self._preview_after = self.after(1200, submit)

    def on_lines(self, conversation_id, captured):
        if not captured:
            return
        self.invalidate_preview()
        for origin, stamp, text in captured:
            try:
                line = self.store.line(conversation_id, origin, stamp, text)
                if conversation_id == self._get_conversation_id():
                    self._put_line(line)
                if self.enabled_var.get() and not self._closed:
                    self.worker.submit(line, self.config())
            except Exception as exc:
                self.status.set(str(exc) if isinstance(exc, translation.TranslationError) else "Could not index/queue the translation. Original transcript remains saved.")
                self.enabled_var.set(False)

    def load_conversation(self):
        self.invalidate_preview()
        self._lines = {}
        self.tree.delete(*self.tree.get_children())
        transcript = self._get_session_file()
        if transcript:
            try:
                self.store.sync_transcript(transcript)
                for line in self.store.lines(transcript.conversation_id):
                    self._put_line(line, state=line.get("last_state") or "Not translated", follow=False, show=False)
                if self._lines:
                    last = next(reversed(self._lines))
                    self.tree.selection_set(last)
                    self.tree.see(last)
                    self._show_selected()
            except Exception:
                self.status.set("Could not load translation history. Original transcript is unchanged.")
        if not self._lines:
            markdown_html.show(self.view, "<p>New transcript lines and their translations will appear here.</p>")

    def _put_line(self, line, *, state=None, follow=True, show=True):
        key = str(line["id"])
        new = not self.tree.exists(key)
        self._lines[key] = line
        if state is None:
            history = self.store.history(line["id"])
            state = f"{history[-1]['engine']}: {history[-1]['status']}" if history else "Not translated"
        values = {"text": line["source_text"][:110], "values": (line["stamp"], state)}
        if new:
            self.tree.insert("", "end", iid=key, **values)
        else:
            self.tree.item(key, **values)
        if new and follow and self.follow_var.get():
            self.tree.selection_set(key)
            self.tree.see(key)
        if show and key in self.tree.selection():
            self._show_selected()

    def _show_selected(self):
        selected = self.tree.selection()
        line = self._lines.get(selected[0]) if selected else None
        if not line:
            return
        e = markdown_html.plain_to_html
        body = f"<div class='meta'>{e(line['stamp'])}</div><p><b>Original</b></p><p>{e(line['source_text'])}</p>"
        for item in self.store.history(line["id"]):
            meta = f"{translation.ENGINES.get(item['engine'], item['engine'])} · {item['source_lang']} → {item['target_lang']} · {item['model']} · {item['created_at']} · {item['status']}"
            body += f"<hr><div class='meta'>{e(meta)}</div><p>{e(item['translated_text'] or item['error'] or 'Waiting…')}</p>"
        markdown_html.show(self.view, body, scroll_to_end=True)

    def translate_selected(self):
        selection = self.tree.selection()
        line = self._lines.get(selection[0]) if selection else None
        if not line:
            return
        try:
            accepted = self.worker.submit(line, self.config(), force=True)
            self.status.set("Translation queued." if accepted else "This line is already queued or translating.")
            self._put_line(line)
        except translation.TranslationError as exc:
            messagebox.showinfo("Translation", str(exc), parent=self)

    def translate_missing(self):
        try:
            config = self.config()
        except translation.TranslationError as exc:
            messagebox.showinfo("Translation", str(exc), parent=self)
            return
        lines = self.store.missing(self._get_conversation_id(), config)
        if not lines:
            self.status.set("No missing translations for this engine/model/language/prompt.")
            return
        if not messagebox.askyesno("Translate missing", f"Translate {len(lines)} line(s) with {self.engine_var.get()}?\nCloud requests consume your quota and may be billed. No provider fallback.", parent=self):
            return
        self.worker.resume()
        count = 0
        try:
            for line in lines:
                count += int(self.worker.submit(line, config))
        except translation.TranslationError as exc:
            self.status.set(f"Queued {count} lines. {exc}")
        else:
            self.status.set(f"Queued {count} lines.")

    def stop_translation(self):
        self.enabled_var.set(False)
        self.invalidate_preview()
        self.worker.cancel_all()
        self.persist_settings()
        self.status.set("Stopped translation. An in-flight cloud request may already have been received; original text is preserved.")

    def _utility(self, job, message):
        if self._utility_busy:
            return
        self._utility_busy = True
        self.test_btn.configure(state="disabled")
        self.download_btn.configure(state="disabled")
        self.status.set(message)

        def worker():
            try:
                result = job()
            except Exception as exc:
                result = str(exc) if isinstance(exc, translation.TranslationError) else "Operation failed. Check dependencies, network or the model download, then retry."
            self._results.put(result)

        threading.Thread(target=worker, name="translation-check", daemon=True).start()

    def test_connection(self):
        try:
            config = self.config()
        except translation.TranslationError as exc:
            messagebox.showinfo("Translation", str(exc), parent=self)
            return
        text = "This is a translation test." if config.source == "en" else ("Đây là câu kiểm tra dịch." if config.source == "vi" else "Hello")
        self._utility(lambda: "Test result: " + self.worker.translator.translate(text, config), "Testing selected engine with a short sample sentence…")

    def download_model(self):
        if not messagebox.askyesno("Download VinAI model", "Download the VinAI English → Vietnamese model from Hugging Face?\nThis is a large download (several GB may be required). Optional libraries must be installed first.\nLicense: AGPL-3.0. No conversation text is uploaded.", parent=self):
            return
        self._utility(lambda: (translation.download_offline(), "Model downloaded. Press Test connection / model to check it.")[1], "Downloading VinAI model… this may take several minutes.")

    def _drain(self):
        if self._closed:
            return
        for _ in range(30):
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            self._utility_busy = False
            self.test_btn.configure(state="normal")
            self.download_btn.configure(state="normal")
            self.status.set(result)
        for _ in range(30):
            try:
                task, status, value = self.worker.results.get_nowait()
            except queue.Empty:
                break
            if status == "error":
                self.enabled_var.set(False)
                self.invalidate_preview()
                self.settings.translation_enabled = False
                self.status.set(f"Translation paused — {value}")
            if task.preview:
                if task.revision == self._revision and task.line["conversation_id"] == self._get_conversation_id():
                    self.preview_label.configure(text=f"Provisional ({task.config.engine}): {task.line['source_text']}\n{value}")
            elif task.line["conversation_id"] == self._get_conversation_id():
                try:
                    self._put_line(task.line)
                except Exception:
                    self.enabled_var.set(False)
                    self.settings.translation_enabled = False
                    self.invalidate_preview()
                    self.worker.cancel_all()
                    self.status.set("Could not refresh translation history. Check SQLite access; original transcript remains saved.")
                    continue
                if status != "error":
                    self.status.set(f"{task.config.engine}: {status}" + (f" — {value}" if status != "done" else ""))
        self.after(100, self._drain)

    def close(self):
        self.invalidate_preview()
        self._closed = True
        self.worker.close()
