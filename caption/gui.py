"""Tkinter UI: a control window with the full transcript plus a floating caption overlay."""

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from . import chatgpt, livecaptions, markdown_html, prompt_versions
from .audio import AudioCapture, AudioDevice, list_devices
from .chat_tab import ChatPanel
from .livecaptions import LiveCaptionsReader
from .overlay import CaptionOverlay
from .prompt_editor import PromptEditor
from .settings import Settings
from .transcript_file import TRANSCRIPTS_DIR, TranscriptFile
from .transcriber import Event, StreamingTranscriber, TranscriberConfig

log = logging.getLogger(__name__)

MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "distil-large-v3", "large-v3-turbo"]
COMPUTE = ["auto", "cuda", "cpu"]
POLL_MS = 50


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        # Each event carries the transcript file of the session that produced it, so text
        # flushed after Stop still lands in the right file even if a new session started.
        self.events: "queue.Queue[tuple[TranscriptFile, Event]]" = queue.Queue()
        self._open_files: set[TranscriptFile] = set()
        self.session_file: TranscriptFile | None = None  # file of the source being captured now
        self._sending_to_chatgpt = False
        self._ui_calls: "queue.Queue[Callable[[], None]]" = queue.Queue()  # from worker threads
        self.devices: list[AudioDevice] = []
        self.capture: AudioCapture | None = None
        self.transcriber: StreamingTranscriber | LiveCaptionsReader | None = None
        self._line_open = False  # transcript's current line already has text

        root.title("Real-time Caption")
        # Tk sizes are in physical pixels once the process is DPI-aware, so scale them.
        dpi_scale = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        root.geometry(f"{int(900 * dpi_scale)}x{int(720 * dpi_scale)}")
        root.minsize(int(640 * dpi_scale), int(480 * dpi_scale))
        self._build_ui()

        self.overlay = CaptionOverlay(root, self.settings)
        self.overlay.on_hidden = lambda: self.overlay_var.set(False)
        self.overlay.on_click_through = self._set_click_through
        self.overlay.transparent_var.trace_add(
            "write", lambda *_: self.transparent_var.set(self.overlay.transparent_var.get())
        )
        if self.settings.show_overlay:
            self.overlay.show()

        self._set_always_on_top(self.settings.always_on_top)
        self.refresh_devices()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(POLL_MS, self._poll)

    # --------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Source").grid(row=0, column=0, sticky="w", **pad)
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(top, textvariable=self.device_var, state="readonly")
        self.device_box.grid(row=0, column=1, columnspan=4, sticky="ew", **pad)
        self.refresh_btn = ttk.Button(top, text="Refresh", command=self.refresh_devices)
        self.refresh_btn.grid(row=0, column=5, sticky="ew", **pad)

        ttk.Label(top, text="Model").grid(row=1, column=0, sticky="w", **pad)
        self.model_var = tk.StringVar(value=self.settings.model)
        self.model_box = ttk.Combobox(top, textvariable=self.model_var, values=MODELS, width=18)
        self.model_box.grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(top, text="Run on").grid(row=1, column=2, sticky="e", **pad)
        self.compute_var = tk.StringVar(value=self.settings.compute)
        self.compute_box = ttk.Combobox(
            top, textvariable=self.compute_var, values=COMPUTE, state="readonly", width=8
        )
        self.compute_box.grid(row=1, column=3, sticky="w", **pad)
        self.start_btn = ttk.Button(top, text="▶ Start", command=self.toggle)
        self.start_btn.grid(row=1, column=5, sticky="ew", **pad)

        ttk.Label(top, text="Overlay").grid(row=2, column=0, sticky="w", **pad)
        overlay_opts = ttk.Frame(top)
        overlay_opts.grid(row=2, column=1, columnspan=5, sticky="w")
        self.overlay_var = tk.BooleanVar(value=self.settings.show_overlay)
        ttk.Checkbutton(
            overlay_opts, text="Show", variable=self.overlay_var, command=self._toggle_overlay
        ).pack(side="left", **pad)
        self.transparent_var = tk.BooleanVar(value=self.settings.overlay_transparent)
        ttk.Checkbutton(
            overlay_opts,
            text="Transparent (text only)",
            variable=self.transparent_var,
            command=lambda: self.overlay.set_transparent(self.transparent_var.get()),
        ).pack(side="left", **pad)
        self.click_through_var = tk.BooleanVar(value=self.settings.overlay_click_through)
        ttk.Checkbutton(
            overlay_opts,
            text="Click-through (lock position)",
            variable=self.click_through_var,
            command=lambda: self._set_click_through(self.click_through_var.get()),
        ).pack(side="left", **pad)

        ttk.Label(top, text="ChatGPT").grid(row=3, column=0, sticky="w", **pad)
        chatgpt_opts = ttk.Frame(top)
        chatgpt_opts.grid(row=3, column=1, columnspan=5, sticky="w")
        ttk.Label(chatgpt_opts, text="Power").pack(side="left", padx=(6, 4))
        level = self._chatgpt_power()
        self.power_var = tk.StringVar(value=self._power_choices()[0 if level is None else level + 1])
        power_box = ttk.Combobox(
            chatgpt_opts, textvariable=self.power_var, values=self._power_choices(), state="readonly", width=16
        )
        power_box.pack(side="left", padx=(0, 12))
        power_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: setattr(self.settings, "chatgpt_power", self._power_choices().index(self.power_var.get()) - 1),
        )
        self.new_chat_var = tk.BooleanVar(value=self.settings.chatgpt_new_chat)
        ttk.Checkbutton(
            chatgpt_opts,
            text="New chat for each Summarize",
            variable=self.new_chat_var,
            command=lambda: setattr(self.settings, "chatgpt_new_chat", self.new_chat_var.get()),
        ).pack(side="left", **pad)
        ttk.Label(chatgpt_opts, text="Send the last").pack(side="left", padx=(18, 4))
        self.max_lines_var = tk.StringVar(value=str(self.settings.chatgpt_max_lines))
        ttk.Spinbox(
            chatgpt_opts, from_=0, to=100_000, increment=50, width=7, textvariable=self.max_lines_var
        ).pack(side="left")
        ttk.Label(chatgpt_opts, text="caption lines (0 = whole file)").pack(side="left", padx=4)

        ttk.Label(top, text="Summary prompt").grid(row=4, column=0, sticky="w", **pad)
        prompt_opts = ttk.Frame(top)
        prompt_opts.grid(row=4, column=1, columnspan=5, sticky="w")
        self.prompt_var = tk.StringVar(value=prompt_versions.resolve(self.settings.summary_prompt) or "")
        self.prompt_box = ttk.Combobox(
            prompt_opts, textvariable=self.prompt_var, state="readonly", width=40,
            postcommand=self._refresh_prompt_versions,
        )
        self.prompt_box.pack(side="left", **pad)
        self.prompt_box.bind(
            "<<ComboboxSelected>>", lambda _e: setattr(self.settings, "summary_prompt", self.prompt_var.get())
        )
        ttk.Button(prompt_opts, text="Edit / new version…", command=self.edit_summary_prompt).pack(side="left", **pad)
        ttk.Button(
            prompt_opts, text="📂", width=3, command=lambda: self._open_folder(prompt_versions.SUMMARY_DIR)
        ).pack(side="left")
        self._refresh_prompt_versions()

        ttk.Label(top, text="Window").grid(row=5, column=0, sticky="w", **pad)
        self.on_top_var = tk.BooleanVar(value=self.settings.always_on_top)
        ttk.Checkbutton(
            top,
            text="Always on top (stay visible when you click other apps)",
            variable=self.on_top_var,
            command=lambda: self._set_always_on_top(self.on_top_var.get()),
        ).grid(row=5, column=1, columnspan=5, sticky="w", **pad)

        status = ttk.Frame(self.root, padding=(8, 0))
        status.pack(fill="x")
        self.level = ttk.Progressbar(status, maximum=100, length=140)
        self.level.pack(side="left", padx=6)
        self.summarize_btn = ttk.Button(
            status, text="🤖 Summarize (ChatGPT)", command=self.summarize_with_chatgpt, state="disabled"
        )
        self.summarize_btn.pack(side="right", padx=6)
        ttk.Button(status, text="⭳ Export text…", command=self.export_transcript).pack(side="right", padx=6)
        ttk.Button(status, text="📂 Saved files", command=self.open_transcripts_folder).pack(side="right", padx=6)
        ttk.Button(status, text="Clear", command=self.clear_transcript).pack(side="right", padx=6)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=6)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=8)

        self.transcript = ScrolledText(
            self.tabs, wrap="word", font=("Segoe UI", 11), padx=8, pady=6, state="disabled"
        )
        self.transcript.tag_configure("time", foreground="#888888")
        self.transcript.tag_configure("partial", foreground="#8a8a8a")
        self.tabs.add(self.transcript, text="Transcript")

        summary_tab = ttk.Frame(self.tabs)
        summary_bar = ttk.Frame(summary_tab, padding=(0, 4))
        summary_bar.pack(fill="x")
        ttk.Button(summary_bar, text="Copy", command=self.copy_latest_summary).pack(side="left", padx=4)
        ttk.Button(summary_bar, text="Clear", command=self.clear_summaries).pack(side="left", padx=4)
        self.summary = markdown_html.make_view(summary_tab)
        self.summary.pack(fill="both", expand=True)
        self.tabs.add(summary_tab, text="ChatGPT summary")
        self._summary_tab = summary_tab
        self._latest_summary = ""

        self.chat = ChatPanel(
            self.tabs,
            get_session_file=lambda: self.session_file,
            get_max_lines=self._chatgpt_max_lines,
            get_power=self._chatgpt_power,
            run_chatgpt=self._run_chatgpt,
        )
        self.tabs.add(self.chat, text="Chat")
        self.tabs.bind(
            "<<NotebookTabChanged>>",
            lambda _e: self.chat.entry.focus_set() if self.tabs.select() == str(self.chat) else None,
        )

    def refresh_devices(self) -> None:
        try:
            self.devices = list_devices()
        except Exception as exc:  # noqa: BLE001
            log.exception("Listing audio devices failed")
            messagebox.showerror("Audio devices", f"Could not list audio devices:\n{exc}")
            self.devices = []
        labels = [d.label for d in self.devices] + [livecaptions.LABEL]
        self.device_box["values"] = labels
        if self.device_var.get() in labels:
            return
        preferred = self.settings.device_label
        self.device_var.set(preferred if preferred in labels else (labels[0] if labels else ""))

    def _toggle_overlay(self) -> None:
        if self.overlay_var.get():
            self.overlay.show()
        else:
            self.overlay.win.withdraw()

    def _set_always_on_top(self, enabled: bool) -> None:
        self.settings.always_on_top = enabled
        self.root.attributes("-topmost", enabled)

    def _set_click_through(self, enabled: bool) -> None:
        self.click_through_var.set(enabled)
        self.overlay.set_click_through(enabled)

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(text="■ Stop" if running else "▶ Start")
        state = "disabled" if running else "readonly"
        self.device_box.configure(state=state)
        self.compute_box.configure(state=state)
        self.model_box.configure(state="disabled" if running else "normal")
        self.refresh_btn.configure(state="disabled" if running else "normal")
        self._update_chatgpt_controls()

    def _update_chatgpt_controls(self) -> None:
        enabled = self.session_file is not None and not self._sending_to_chatgpt
        self.summarize_btn.configure(state="normal" if enabled else "disabled")
        self.chat.set_busy(self._sending_to_chatgpt)

    # -------------------------------------------------------------- start/stop

    def toggle(self) -> None:
        if self.transcriber:
            self.stop()
        else:
            self.start()

    def _new_session_file(self, source_label: str):
        """Create this session's auto-save file; returns (file, event sink) or None on failure."""
        try:
            tfile = TranscriptFile(source_label)
        except OSError as exc:
            log.exception("Could not create transcript file")
            messagebox.showerror("Real-time Caption", f"Could not create the transcript file:\n{exc}")
            return None
        self._open_files.add(tfile)
        return tfile, lambda event: self.events.put((tfile, event))

    def _close_session_file(self, tfile: TranscriptFile) -> None:
        tfile.close()
        self._open_files.discard(tfile)

    def start(self) -> None:
        if self.device_var.get() == livecaptions.LABEL:
            session = self._new_session_file(livecaptions.LABEL)
            if session is None:
                return
            tfile, sink = session
            self.transcriber = LiveCaptionsReader(sink)
            self.transcriber.start()
            self.session_file = tfile
            self.settings.device_label = livecaptions.LABEL
            self._set_running(True)
            self.status_var.set("Connecting to Windows Live Captions…")
            return

        device = next((d for d in self.devices if d.label == self.device_var.get()), None)
        if device is None:
            messagebox.showwarning("Real-time Caption", "Please choose an audio source.")
            return
        model = self.model_var.get().strip() or "small.en"

        session = self._new_session_file(device.label)
        if session is None:
            return
        tfile, sink = session
        audio_queue: queue.Queue = queue.Queue()
        self.transcriber = StreamingTranscriber(
            TranscriberConfig(model=model, device=self.compute_var.get()), audio_queue, sink
        )
        self.capture = AudioCapture(device, audio_queue)
        try:
            self.capture.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Opening audio device failed")
            self.capture = None
            self.transcriber = None
            self._close_session_file(tfile)
            messagebox.showerror("Audio", f"Could not open {device.label}:\n{exc}")
            return
        self.transcriber.start()
        self.session_file = tfile

        self.settings.device_label = device.label
        self.settings.model = model
        self.settings.compute = self.compute_var.get()
        self._set_running(True)
        self.status_var.set("Starting…")

    def stop(self) -> None:
        if self.capture:
            self.capture.stop()
            self.capture = None
        if self.transcriber:
            self.transcriber.stop()  # flushes the last utterance, then emits "stopped"
            self.transcriber = None
        self.session_file = None
        self._set_running(False)
        self.status_var.set("Stopped")

    # --------------------------------------------------------------- ChatGPT

    def _run_chatgpt(
        self,
        job: Callable[[Callable[[str], None]], str],
        on_answer: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> bool:
        """Run one ChatGPT request on a worker thread; only one may run at a time (shared Chrome).

        ``job(status)`` returns the answer; ``on_answer``/``on_error`` run on the UI thread.
        Returns False if another request is still running.
        """
        if self._sending_to_chatgpt:
            return False
        self._sending_to_chatgpt = True
        self._update_chatgpt_controls()

        def status(msg: str) -> None:
            self._ui_calls.put(lambda: self.status_var.set(f"ChatGPT: {msg}"))

        def finish(callback: Callable[[], None]) -> None:
            self._sending_to_chatgpt = False
            self._update_chatgpt_controls()
            callback()

        def work() -> None:
            try:
                answer = job(status)
            except Exception as exc:  # noqa: BLE001
                log.exception("Asking ChatGPT failed")
                message = str(exc) if isinstance(exc, chatgpt.ChatGPTError) else f"Asking ChatGPT failed:\n{exc}"
                self._ui_calls.put(lambda: finish(lambda: (self.status_var.set("ChatGPT: failed"), on_error(message))))
            else:
                self._ui_calls.put(lambda: finish(lambda: (self.status_var.set("ChatGPT: answer received"), on_answer(answer))))

        threading.Thread(target=work, name="chatgpt", daemon=True).start()
        return True

    @staticmethod
    def _power_choices() -> list[str]:
        return ["Keep ChatGPT's", *chatgpt.POWER_LEVELS]

    def _chatgpt_power(self) -> int | None:
        """Power level to set before sending, or None to leave ChatGPT's slider alone."""
        level = self.settings.chatgpt_power
        return level if 0 <= level < len(chatgpt.POWER_LEVELS) else None

    def _refresh_prompt_versions(self) -> None:
        """Re-read prompts/summarize/ (files may have been added by hand) and keep a valid choice."""
        versions = prompt_versions.list_versions()
        self.prompt_box["values"] = versions
        chosen = prompt_versions.resolve(self.prompt_var.get())
        self.prompt_var.set(chosen or "")
        self.settings.summary_prompt = chosen or ""

    def edit_summary_prompt(self) -> None:
        base = prompt_versions.resolve(self.prompt_var.get())
        if base is None:
            messagebox.showerror("Summary prompt", f"No summary prompt found in {prompt_versions.SUMMARY_DIR}.")
            return

        def saved(name: str) -> None:
            self.prompt_var.set(name)
            self._refresh_prompt_versions()
            self.status_var.set(f"Saved summary prompt {name} — Summarize will now use it")

        PromptEditor(self.root, base, saved)

    def _chatgpt_max_lines(self) -> int:
        """Caption lines to send (newest first kept); 0 = whole file. Invalid input keeps the last value."""
        try:
            value = max(0, int(self.max_lines_var.get().strip()))
        except ValueError:
            value = self.settings.chatgpt_max_lines
        self.settings.chatgpt_max_lines = value
        self.max_lines_var.set(str(value))
        return value

    def summarize_with_chatgpt(self) -> None:
        """Send the running session's transcript file to ChatGPT with the editable summary prompt."""
        tfile = self.session_file
        if tfile is None or self._sending_to_chatgpt:
            return
        if not tfile.has_text:
            messagebox.showinfo("Summarize", "No captions have been saved for this session yet.")
            return
        try:
            upload = tfile.snapshot(self._chatgpt_max_lines())
            version = prompt_versions.resolve(self.prompt_var.get())
            if version is None:
                raise chatgpt.ChatGPTError(f"No summary prompt found in {prompt_versions.SUMMARY_DIR}.")
            prompt = chatgpt.build_prompt(upload, tfile.source, tfile.started, prompt_versions.path_for(version))
        except (OSError, chatgpt.ChatGPTError) as exc:
            messagebox.showerror("Summarize", str(exc))
            return
        # Same ChatGPT conversation as the Chat tab; "New conversation" there also applies here.
        new_chat = self.new_chat_var.get() or self.chat.wants_new_conversation
        power = self._chatgpt_power()
        self._run_chatgpt(
            lambda status: chatgpt.ask(prompt, upload, status, new_chat=new_chat, power=power),
            lambda answer: self._summary_received(tfile, answer, new_chat),
            lambda error: messagebox.showerror("Summarize with ChatGPT", error),
        )

    def _summary_received(self, tfile: TranscriptFile, answer: str, started_new_chat: bool) -> None:
        self.chat.add_summary_exchange(tfile.path.name, answer, started_new_chat)
        self._show_summary(tfile, answer)
        saved = self._save_summary(tfile, answer)
        self.status_var.set("ChatGPT: summary received" + (f" — saved to {saved.name}" if saved else ""))

    def _show_summary(self, tfile: TranscriptFile, answer: str) -> None:
        """Replace the displayed summary with the new one (history stays in the .summary.md file)."""
        self._latest_summary = answer
        meta = markdown_html.plain_to_html(f"Received {datetime.now():%H:%M:%S} · {tfile.path.name}")
        markdown_html.show(self.summary, f"<div class='meta'>{meta}</div>" + markdown_html.to_html(answer))
        self.tabs.select(self._summary_tab)

    def _save_summary(self, tfile: TranscriptFile, answer: str):
        path = tfile.path.with_name(tfile.path.stem + ".summary.md")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"<!-- ChatGPT summary, {datetime.now():%Y-%m-%d %H:%M:%S} -->\n\n{answer}\n\n---\n\n")
        except OSError:
            log.exception("Could not save the ChatGPT summary")
            return None
        return path

    def copy_latest_summary(self) -> None:
        if not self._latest_summary:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._latest_summary)
        self.status_var.set("ChatGPT summary copied to the clipboard")

    def clear_summaries(self) -> None:
        self._latest_summary = ""
        markdown_html.show(self.summary, "")

    # ------------------------------------------------------------ event pump

    def _poll(self) -> None:
        try:
            while True:
                self._handle(*self.events.get_nowait())
        except queue.Empty:
            pass
        while True:
            try:
                call = self._ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                call()
            except Exception:  # noqa: BLE001 - never let one callback stop the UI loop
                log.exception("UI callback failed")

        level =self.capture.level if self.capture else 0.0
        db = 20 * math.log10(level) if level > 1e-6 else -90.0
        self.level["value"] = max(0.0, min(100.0, (db + 60) / 60 * 100))
        if self.capture:
            self.capture.level *= 0.6  # decay when no new audio arrives (silent loopback)
        self.overlay.tick()
        self.root.after(POLL_MS, self._poll)

    def _handle(self, tfile: TranscriptFile, event: Event) -> None:
        if event.kind == "status":
            self.status_var.set(event.text)
        elif event.kind == "ready":
            self.status_var.set(f"{event.text} — saving to {tfile.path.name}")
        elif event.kind == "partial":
            self._set_transcript_partial(event.text)
            self.overlay.set_partial(event.text)
        elif event.kind == "final":
            tfile.write(event.text, event.end_of_utterance)
            self._append_transcript(event.text, event.end_of_utterance)
            self.overlay.add_final(event.text)
        elif event.kind == "error":
            self.status_var.set("Error")
            messagebox.showerror("Real-time Caption", event.text)
            if self.transcriber:
                self.stop()
        elif event.kind == "stopped":
            self._close_session_file(tfile)
            self._set_transcript_partial("")

    # ------------------------------------------------------------- transcript

    def _set_transcript_partial(self, text: str) -> None:
        t = self.transcript
        t.configure(state="normal")
        ranges = t.tag_ranges("partial")
        if ranges:
            t.delete(ranges[0], ranges[-1])
        if text:
            if not self._line_open:
                t.insert("end", datetime.now().strftime("[%H:%M:%S] "), "time")
                self._line_open = True
            t.insert("end", text, "partial")
        t.configure(state="disabled")
        t.see("end")

    def _append_transcript(self, text: str, end_of_utterance: bool) -> None:
        self._set_transcript_partial("")
        t = self.transcript
        t.configure(state="normal")
        if not self._line_open:
            t.insert("end", datetime.now().strftime("[%H:%M:%S] "), "time")
        t.insert("end", text + ("\n" if end_of_utterance else " "))
        self._line_open = not end_of_utterance
        t.configure(state="disabled")
        t.see("end")

    def clear_transcript(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self._line_open = False
        self.overlay.clear()

    def export_transcript(self) -> None:
        """Write the whole transcript, including the sentence still being heard, to a text file."""
        content = self.transcript.get("1.0", "end-1c").strip()
        if not content:
            messagebox.showinfo("Export text", "There is no text to export yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Export text",
            defaultextension=".txt",
            initialfile=datetime.now().strftime("transcript_%Y%m%d_%H%M%S.txt"),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
        except OSError as exc:
            messagebox.showerror("Export text", f"Could not write {path}:\n{exc}")
            return
        self.status_var.set(f"Exported to {path}")

    def open_transcripts_folder(self) -> None:
        self._open_folder(TRANSCRIPTS_DIR)

    @staticmethod
    def _open_folder(folder) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def on_close(self) -> None:
        self._chatgpt_max_lines()  # store the spinbox value in settings
        self.stop()
        for tfile in list(self._open_files):
            self._close_session_file(tfile)
        self.settings.show_overlay = self.overlay_var.get()
        self.settings.model = self.model_var.get().strip() or self.settings.model
        self.settings.compute = self.compute_var.get()
        if self.device_var.get():
            self.settings.device_label = self.device_var.get()
        self.settings.save()
        self.root.destroy()
