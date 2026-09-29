"""Tkinter UI: a control window with the full transcript plus a floating caption overlay."""

from __future__ import annotations

import logging
import math
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from . import chatgpt, livecaptions, markdown_html, prompt_versions, screenshot
from .audio import AudioCapture, AudioDevice, list_devices
from .chat_tab import ChatPanel
from .conversations import ChatMessage, Conversation, ConversationStore, ConversationTranscript
from .vocab_tab import VocabPanel
from .livecaptions import LiveCaptionsReader
from .overlay import CaptionOverlay
from .prompt_editor import PromptEditor
from .settings import Settings
from .transcript_file import TRANSCRIPTS_DIR
from .transcriber import Event, StreamingTranscriber, TranscriberConfig

log = logging.getLogger(__name__)

MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "distil-large-v3", "large-v3-turbo"]
COMPUTE = ["auto", "cuda", "cpu"]
POLL_MS = 50


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.store = ConversationStore()
        imported = self.store.import_legacy()
        if imported:
            log.info("Imported %s legacy transcript(s) into conversations", imported)
        # Each event carries the transcript file of the session that produced it, so text
        # flushed after Stop still lands in the right file even if a new session started.
        self.events: "queue.Queue[tuple[ConversationTranscript, Event]]" = queue.Queue()
        self._open_files: set[ConversationTranscript] = set()
        self.current_conversation: Conversation | None = None
        self.session_file: ConversationTranscript | None = None
        self._sending_to_chatgpt = False
        self._ui_calls: "queue.Queue[Callable[[], None]]" = queue.Queue()  # from worker threads
        self.devices: list[AudioDevice] = []
        self.capture: AudioCapture | None = None
        self.transcriber: StreamingTranscriber | LiveCaptionsReader | None = None
        self._capture_file: ConversationTranscript | None = None
        self._stopping = False
        self._line_open = False  # transcript's current line already has text
        self._closing = False

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
        self._refresh_conversations()
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
        self.start_btn = ttk.Button(top, text="▶ Start new", command=self.toggle)
        self.start_btn.grid(row=1, column=5, sticky="ew", **pad)

        status = ttk.Frame(self.root, padding=(8, 0))
        status.pack(fill="x")
        self.level = ttk.Progressbar(status, maximum=100, length=140)
        self.level.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(status, textvariable=self.status_var).pack(side="left", padx=6)

        workspace = ttk.Frame(self.root)
        workspace.pack(fill="both", expand=True, padx=(6, 10), pady=8)

        activity = ttk.Frame(workspace, width=58, padding=(2, 4))
        activity.pack(side="left", fill="y")
        activity.pack_propagate(False)
        ttk.Button(
            activity, text="☰\nChats", width=7, command=lambda: self._show_sidebar("conversations")
        ).pack(fill="x", pady=2)
        ttk.Button(
            activity, text="⚙\nSettings", width=7, command=lambda: self._show_sidebar("settings")
        ).pack(fill="x", pady=2)

        self.sidebar = ttk.Frame(workspace, width=270, padding=(4, 0))
        self.sidebar.pack(side="left", fill="y", padx=(0, 6))
        self.sidebar.pack_propagate(False)
        sidebar_header = ttk.Frame(self.sidebar)
        sidebar_header.pack(fill="x", pady=(2, 5))
        self.sidebar_title = tk.StringVar(value="Conversations")
        ttk.Label(sidebar_header, textvariable=self.sidebar_title, font=("Segoe UI", 11, "bold")).pack(
            side="left", padx=4
        )
        ttk.Button(sidebar_header, text="×", width=3, command=self._hide_sidebar).pack(side="right")
        sidebar_content = ttk.Frame(self.sidebar)
        sidebar_content.pack(fill="both", expand=True)

        conversations = ttk.Frame(sidebar_content)
        conversations.pack_propagate(False)
        conversation_actions = ttk.Frame(conversations)
        conversation_actions.pack(fill="x", padx=2, pady=(0, 6))
        self.new_conversation_btn = ttk.Button(
            conversation_actions, text="+ New & start", command=self.new_conversation
        )
        self.new_conversation_btn.pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(conversation_actions, text="Rename", command=self.rename_conversation).pack(side="left", padx=2)
        self.conversation_tree = ttk.Treeview(
            conversations, columns=("updated",), show="tree headings", selectmode="browse", height=12
        )
        self.conversation_tree.heading("#0", text="Name")
        self.conversation_tree.heading("updated", text="Last used")
        self.conversation_tree.column("#0", width=155, minwidth=100)
        self.conversation_tree.column("updated", width=82, minwidth=70, anchor="center")
        self.conversation_tree.pack(fill="both", expand=True)
        self.conversation_tree.bind("<<TreeviewSelect>>", self._conversation_selected)
        ttk.Button(conversations, text="Open conversation folder", command=self.open_conversation_folder).pack(
            fill="x", padx=4, pady=6
        )

        settings_panel = ttk.Frame(sidebar_content, padding=(8, 4))
        ttk.Label(settings_panel, text="Overlay", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 5))
        self.overlay_var = tk.BooleanVar(value=self.settings.show_overlay)
        ttk.Checkbutton(
            settings_panel, text="Show caption overlay", variable=self.overlay_var, command=self._toggle_overlay
        ).pack(anchor="w", pady=3)
        self.transparent_var = tk.BooleanVar(value=self.settings.overlay_transparent)
        ttk.Checkbutton(
            settings_panel,
            text="Transparent (text only)",
            variable=self.transparent_var,
            command=lambda: self.overlay.set_transparent(self.transparent_var.get()),
        ).pack(anchor="w", pady=3)
        self.click_through_var = tk.BooleanVar(value=self.settings.overlay_click_through)
        ttk.Checkbutton(
            settings_panel,
            text="Click-through (lock position)",
            variable=self.click_through_var,
            command=lambda: self._set_click_through(self.click_through_var.get()),
        ).pack(anchor="w", pady=3)
        ttk.Separator(settings_panel).pack(fill="x", pady=10)
        ttk.Label(settings_panel, text="Window", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 5))
        self.on_top_var = tk.BooleanVar(value=self.settings.always_on_top)
        ttk.Checkbutton(
            settings_panel,
            text="Always on top",
            variable=self.on_top_var,
            command=lambda: self._set_always_on_top(self.on_top_var.get()),
        ).pack(anchor="w", pady=3)
        ttk.Button(settings_panel, text="Save settings", command=self.save_settings).pack(
            fill="x", pady=(14, 4)
        )

        self._sidebar_pages = {"conversations": conversations, "settings": settings_panel}
        self._sidebar_name: str | None = None

        main_area = ttk.Frame(workspace)
        main_area.pack(side="left", fill="both", expand=True)
        self._main_area = main_area
        self.tabs = ttk.Notebook(main_area)
        self.tabs.pack(fill="both", expand=True)
        self._show_sidebar("conversations")

        transcript_tab = ttk.Frame(self.tabs)
        transcript_actions = ttk.Frame(transcript_tab, padding=(4, 4))
        transcript_actions.pack(fill="x")
        ttk.Button(transcript_actions, text="⭳ Export text…", command=self.export_transcript).pack(
            side="right", padx=4
        )
        ttk.Button(transcript_actions, text="📂 Saved files", command=self.open_transcripts_folder).pack(
            side="right", padx=4
        )
        ttk.Button(transcript_actions, text="Clear", command=self.clear_transcript).pack(side="right", padx=4)
        self.transcript = ScrolledText(
            transcript_tab, wrap="word", font=("Segoe UI", 11), padx=8, pady=6, state="disabled"
        )
        self.transcript.tag_configure("time", foreground="#888888")
        self.transcript.tag_configure("partial", foreground="#8a8a8a")
        self.transcript.pack(fill="both", expand=True)
        self.tabs.add(transcript_tab, text="Transcript")

        summary_tab = ttk.Frame(self.tabs)
        summary_actions = ttk.Frame(summary_tab, padding=(4, 4))
        summary_actions.pack(fill="x")
        self.summarize_btn = ttk.Button(
            summary_actions,
            text="🤖 Summarize (ChatGPT)",
            command=self.summarize_with_chatgpt,
            state="disabled",
        )
        self.summarize_btn.pack(side="left", padx=4)
        ttk.Button(summary_actions, text="Copy selected", command=self.copy_latest_summary).pack(side="right", padx=4)
        ttk.Button(summary_actions, text="Hide content", command=self.clear_summaries).pack(side="right", padx=4)

        summary_body = ttk.Frame(summary_tab)
        summary_body.pack(fill="both", expand=True)
        summary_activity = ttk.Frame(summary_body, width=58, padding=(2, 3))
        summary_activity.pack(side="left", fill="y")
        summary_activity.pack_propagate(False)
        ttk.Button(
            summary_activity, text="☷\nHistory", width=7, command=lambda: self._show_summary_sidebar("history")
        ).pack(fill="x", pady=2)
        ttk.Button(
            summary_activity, text="⚙\nSettings", width=7, command=lambda: self._show_summary_sidebar("settings")
        ).pack(fill="x", pady=2)

        self.summary_sidebar = ttk.Frame(summary_body, width=290, padding=(4, 0))
        self.summary_sidebar.pack(side="left", fill="y", padx=(0, 5))
        self.summary_sidebar.pack_propagate(False)
        summary_header = ttk.Frame(self.summary_sidebar)
        summary_header.pack(fill="x", pady=(2, 5))
        self.summary_sidebar_title = tk.StringVar(value="Saved summaries")
        ttk.Label(
            summary_header, textvariable=self.summary_sidebar_title, font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=4)
        ttk.Button(summary_header, text="×", width=3, command=self._hide_summary_sidebar).pack(side="right")
        summary_side_content = ttk.Frame(self.summary_sidebar)
        summary_side_content.pack(fill="both", expand=True)

        summary_history = ttk.Frame(summary_side_content)
        self.summary_tree = ttk.Treeview(
            summary_history,
            columns=("created",),
            show="tree headings",
            selectmode="browse",
        )
        self.summary_tree.heading("#0", text="Preview")
        self.summary_tree.heading("created", text="Created")
        self.summary_tree.column("#0", width=150, minwidth=100)
        self.summary_tree.column("created", width=110, minwidth=95, anchor="center")
        self.summary_tree.pack(fill="both", expand=True, pady=(0, 4))
        self.summary_tree.bind("<<TreeviewSelect>>", self._summary_selected)

        summary_settings = ttk.Frame(summary_side_content, padding=(6, 3))
        ttk.Label(summary_settings, text="ChatGPT Power").pack(anchor="w", pady=(2, 2))
        summary_level = self.settings.summary_chatgpt_power
        summary_level = summary_level if -1 <= summary_level < len(chatgpt.POWER_LEVELS) else -1
        self.summary_power_var = tk.StringVar(value=self._power_choices()[summary_level + 1])
        self.summary_power_box = ttk.Combobox(
            summary_settings,
            textvariable=self.summary_power_var,
            values=self._power_choices(),
            state="readonly",
        )
        self.summary_power_box.pack(fill="x", pady=(0, 8))
        self.summary_power_box.bind("<<ComboboxSelected>>", lambda _e: self._save_summary_power())
        ttk.Label(summary_settings, text="Send the last caption lines (0 = all)").pack(anchor="w", pady=(2, 2))
        self.summary_max_lines_var = tk.StringVar(value=str(self.settings.summary_max_lines))
        ttk.Spinbox(
            summary_settings,
            from_=0,
            to=100_000,
            increment=50,
            textvariable=self.summary_max_lines_var,
        ).pack(fill="x", pady=(0, 8))
        self.new_chat_var = tk.BooleanVar(value=self.settings.summary_new_chat)
        ttk.Checkbutton(
            summary_settings,
            text="New ChatGPT thread for each summary",
            variable=self.new_chat_var,
            command=lambda: setattr(self.settings, "summary_new_chat", self.new_chat_var.get()),
        ).pack(anchor="w", pady=(0, 10))
        ttk.Separator(summary_settings).pack(fill="x", pady=5)
        ttk.Label(summary_settings, text="Summary prompt").pack(anchor="w", pady=(4, 2))
        self.prompt_var = tk.StringVar(value=prompt_versions.resolve(self.settings.summary_prompt) or "")
        self.prompt_box = ttk.Combobox(
            summary_settings,
            textvariable=self.prompt_var,
            state="readonly",
            postcommand=self._refresh_prompt_versions,
        )
        self.prompt_box.pack(fill="x", pady=(0, 5))
        self.prompt_box.bind(
            "<<ComboboxSelected>>", lambda _e: setattr(self.settings, "summary_prompt", self.prompt_var.get())
        )
        prompt_actions = ttk.Frame(summary_settings)
        prompt_actions.pack(fill="x")
        ttk.Button(prompt_actions, text="Edit / new version…", command=self.edit_summary_prompt).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            prompt_actions,
            text="📂",
            width=3,
            command=lambda: self._open_folder(prompt_versions.SUMMARY_DIR),
        ).pack(side="left", padx=(4, 0))
        ttk.Button(summary_settings, text="Save settings", command=self.save_settings).pack(
            fill="x", pady=(12, 4)
        )
        self._refresh_prompt_versions()

        self._summary_sidebar_pages = {"history": summary_history, "settings": summary_settings}
        self._summary_sidebar_name: str | None = None
        self.summary = markdown_html.make_view(summary_body)
        self.summary.pack(side="left", fill="both", expand=True)
        self._summary_main = self.summary
        self._show_summary_sidebar("history")
        self.tabs.add(summary_tab, text="ChatGPT summary")
        self._summary_tab = summary_tab
        self._latest_summary = ""
        self._summary_messages: dict[str, ChatMessage] = {}

        self.chat = ChatPanel(
            self.tabs,
            get_session_file=lambda: self.session_file,
            get_conversation_id=self._conversation_id,
            store=self.store,
            settings=self.settings,
            capture_screen=self._capture_behind_app,
            screenshot_enabled=self.settings.chat_screenshot,
            on_screenshot_toggled=lambda on: setattr(self.settings, "chat_screenshot", on),
            run_chatgpt=self._run_chatgpt,
            on_save_settings=self.save_settings,
        )
        self.tabs.add(self.chat, text="Chat")

        self.vocab = VocabPanel(
            self.tabs,
            settings=self.settings,
            get_session_file=lambda: self.session_file,
            run_chatgpt=self._run_chatgpt,
            on_save_settings=self.save_settings,
        )
        self.tabs.add(self.vocab, text="New words")

        def focus_entry(_event) -> None:
            selected = self.tabs.select()
            if selected == str(self._summary_tab) and self.sidebar.winfo_manager():
                self._hide_sidebar()
            for panel in (self.chat, self.vocab):
                if selected == str(panel):
                    panel.entry.focus_set()

        self.tabs.bind("<<NotebookTabChanged>>", focus_entry)

    def _show_sidebar(self, name: str) -> None:
        if self._sidebar_name == name and self.sidebar.winfo_manager():
            self._hide_sidebar()
            return
        for page in self._sidebar_pages.values():
            page.pack_forget()
        page = self._sidebar_pages[name]
        page.pack(fill="both", expand=True)
        self.sidebar_title.set("Conversations" if name == "conversations" else "Settings")
        if not self.sidebar.winfo_manager():
            self.sidebar.pack(side="left", fill="y", padx=(0, 6), before=self._main_area)
        self._sidebar_name = name

    def _hide_sidebar(self) -> None:
        self.sidebar.pack_forget()

    def _show_summary_sidebar(self, name: str) -> None:
        if self._summary_sidebar_name == name and self.summary_sidebar.winfo_manager():
            self._hide_summary_sidebar()
            return
        for page in self._summary_sidebar_pages.values():
            page.pack_forget()
        self._summary_sidebar_pages[name].pack(fill="both", expand=True)
        self.summary_sidebar_title.set("Saved summaries" if name == "history" else "Summary settings")
        if not self.summary_sidebar.winfo_manager():
            self.summary_sidebar.pack(side="left", fill="y", padx=(0, 5), before=self._summary_main)
        self._summary_sidebar_name = name

    def _hide_summary_sidebar(self) -> None:
        self.summary_sidebar.pack_forget()

    # ---------------------------------------------------------- conversations

    def _conversation_id(self) -> str | None:
        return self.current_conversation.id if self.current_conversation else None

    def _refresh_conversations(self, select_id: str | None = None) -> None:
        conversations = self.store.list()
        wanted = select_id or self._conversation_id() or (conversations[0].id if conversations else None)
        for item in self.conversation_tree.get_children():
            self.conversation_tree.delete(item)
        for conversation in conversations:
            updated = datetime.fromisoformat(conversation.updated_at).strftime("%m-%d %H:%M")
            marker = " ●" if conversation.status == "recording" else ""
            self.conversation_tree.insert(
                "", "end", iid=conversation.id, text=conversation.title + marker, values=(updated,)
            )
        if wanted and self.conversation_tree.exists(wanted):
            self.conversation_tree.selection_set(wanted)
            self.conversation_tree.focus(wanted)
            self._load_conversation(wanted)
        elif not conversations:
            self.current_conversation = None
            self.session_file = None
            self._set_running(False)

    def _conversation_selected(self, _event=None) -> None:
        selected = self.conversation_tree.selection()
        if not selected:
            return
        conversation_id = selected[0]
        if (self.transcriber or self._sending_to_chatgpt) and conversation_id != self._conversation_id():
            if self.current_conversation:
                self.conversation_tree.selection_set(self.current_conversation.id)
            messagebox.showinfo("Conversation", "Pause recording and wait for ChatGPT before switching.")
            return
        self._load_conversation(conversation_id)

    def _load_conversation(self, conversation_id: str) -> None:
        conversation = self.store.get(conversation_id)
        if conversation is None:
            return
        self.current_conversation = conversation
        self.session_file = ConversationTranscript(conversation, self.store)
        if conversation.source in self.device_box["values"]:
            self.device_var.set(conversation.source)
        self._show_saved_transcript()
        self.chat.load_conversation()
        self._refresh_summary_history(select_latest=True)
        self._set_running(self.transcriber is not None)
        self.status_var.set(f"Selected: {conversation.title}")

    def _refresh_summary_history(self, select_latest: bool = False, select_id: int | None = None) -> None:
        """Reload the selected conversation's saved summaries from SQLite."""
        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)
        self._summary_messages = {}
        conversation_id = self._conversation_id()
        summaries = self.store.summaries(conversation_id) if conversation_id else []
        for index, message in reversed(list(enumerate(summaries, start=1))):
            item_id = str(message.id)
            self._summary_messages[item_id] = message
            preview = " ".join(message.content.split())
            if len(preview) > 52:
                preview = preview[:49].rstrip() + "…"
            try:
                created = datetime.fromisoformat(message.created_at).strftime("%m-%d %H:%M")
            except ValueError:
                created = message.created_at
            self.summary_tree.insert(
                "", "end", iid=item_id, text=preview or f"Summary {index}", values=(created,)
            )

        wanted = str(select_id) if select_id is not None else (str(summaries[-1].id) if summaries and select_latest else "")
        if wanted and self.summary_tree.exists(wanted):
            self.summary_tree.selection_set(wanted)
            self.summary_tree.focus(wanted)
            self.summary_tree.see(wanted)
            self._show_summary_message(self._summary_messages[wanted])
        elif not summaries:
            self._latest_summary = ""
            markdown_html.show(self.summary, "<div class='meta'>No saved summaries for this conversation.</div>")

    def _summary_selected(self, _event=None) -> None:
        selected = self.summary_tree.selection()
        if not selected:
            return
        message = self._summary_messages.get(selected[0])
        if message is not None:
            self._show_summary_message(message)

    def _show_summary_message(self, message: ChatMessage) -> None:
        self._latest_summary = message.content
        try:
            created = datetime.fromisoformat(message.created_at).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            created = message.created_at
        meta = markdown_html.plain_to_html(f"Saved {created}")
        markdown_html.show(self.summary, f"<div class='meta'>{meta}</div>" + markdown_html.to_html(message.content))

    def _show_saved_transcript(self) -> None:
        content = self.session_file.read_text() if self.session_file else ""
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        if content:
            self.transcript.insert("1.0", content + "\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")
        self._line_open = False
        self.overlay.clear()

    def new_conversation(self) -> None:
        if self.transcriber or self._sending_to_chatgpt:
            messagebox.showinfo("Conversation", "Pause recording and wait for ChatGPT before starting a new one.")
            return
        source = self.device_var.get()
        if not source:
            messagebox.showwarning("Conversation", "Please choose an audio source first.")
            return
        try:
            conversation = self.store.create(source)
        except OSError as exc:
            messagebox.showerror("Conversation", f"Could not create the conversation:\n{exc}")
            return
        self._refresh_conversations(conversation.id)
        self.start()

    def rename_conversation(self) -> None:
        if self.current_conversation is None:
            return
        if self.transcriber or self._sending_to_chatgpt:
            messagebox.showinfo("Rename", "Pause recording and wait for ChatGPT before renaming it.")
            return
        title = simpledialog.askstring(
            "Rename conversation", "Conversation name:", initialvalue=self.current_conversation.title, parent=self.root
        )
        if title is None:
            return
        try:
            conversation = self.store.rename(self.current_conversation.id, title)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Rename conversation", str(exc))
            return
        self._refresh_conversations(conversation.id)

    def open_conversation_folder(self) -> None:
        if self.current_conversation:
            self._open_folder(self.current_conversation.folder)

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
        if self._stopping:
            self.start_btn.configure(text="Stopping…", state="disabled")
            self.device_box.configure(state="disabled")
            self.compute_box.configure(state="disabled")
            self.model_box.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")
            self.new_conversation_btn.configure(state="disabled")
            self._update_chatgpt_controls()
            return
        idle_text = "▶ Continue" if self.current_conversation else "▶ Start new"
        self.start_btn.configure(text="■ Pause" if running else idle_text, state="normal")
        state = "disabled" if running else "readonly"
        self.device_box.configure(state=state)
        self.compute_box.configure(state=state)
        self.model_box.configure(state="disabled" if running else "normal")
        self.refresh_btn.configure(state="disabled" if running else "normal")
        self.new_conversation_btn.configure(state="disabled" if running else "normal")
        self._update_chatgpt_controls()

    def _update_chatgpt_controls(self) -> None:
        enabled = self.session_file is not None and not self._sending_to_chatgpt
        self.summarize_btn.configure(state="normal" if enabled else "disabled")
        self.chat.set_busy(self._sending_to_chatgpt)
        self.vocab.set_busy(self._sending_to_chatgpt)

    # -------------------------------------------------------------- start/stop

    def toggle(self) -> None:
        if self._stopping:
            return
        if self.transcriber:
            self.stop()
        else:
            if self.current_conversation is None:
                self.new_conversation()
            else:
                self.start()

    def _new_session_file(self, source_label: str):
        """Resume the selected conversation's transcript and return its event sink."""
        if self.current_conversation is None:
            return None
        try:
            tfile = ConversationTranscript(self.current_conversation, self.store)
            tfile.begin_segment(source_label)
            self.store.set_status(self.current_conversation.id, "recording")
        except OSError as exc:
            log.exception("Could not create transcript file")
            messagebox.showerror("Real-time Caption", f"Could not create the transcript file:\n{exc}")
            return None
        self._open_files.add(tfile)
        return tfile, lambda event: self.events.put((tfile, event))

    def _close_session_file(self, tfile: ConversationTranscript) -> None:
        tfile.close()
        self._open_files.discard(tfile)

    def start(self) -> None:
        if self.current_conversation is None:
            self.new_conversation()
            return
        if self.device_var.get() == livecaptions.LABEL:
            session = self._new_session_file(livecaptions.LABEL)
            if session is None:
                return
            tfile, sink = session
            self.transcriber = LiveCaptionsReader(sink)
            self._capture_file = tfile
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
            self.store.set_status(self.current_conversation.id, "paused")
            messagebox.showerror("Audio", f"Could not open {device.label}:\n{exc}")
            return
        self._capture_file = tfile
        self.transcriber.start()
        self.session_file = tfile

        self.settings.device_label = device.label
        self.settings.model = model
        self.settings.compute = self.compute_var.get()
        self._set_running(True)
        self.status_var.set("Starting…")

    def stop(self) -> None:
        if self._stopping:
            return
        if self.capture:
            self.capture.stop()
            self.capture = None
        if self.transcriber:
            self.transcriber.stop()  # flushes the last utterance, then emits "stopped"
            self._stopping = True
            self._set_running(False)
            self.status_var.set("Stopping… waiting for the caption reader to finish")
            return
        self._finish_stop()

    def _finish_stop(self) -> None:
        self.transcriber = None
        self._capture_file = None
        self._stopping = False
        if self.current_conversation:
            self.store.set_status(self.current_conversation.id, "paused")
            refreshed = self.store.get(self.current_conversation.id)
            if refreshed:
                self.current_conversation = refreshed
        self._set_running(False)
        self._refresh_conversations(self._conversation_id())
        self.status_var.set("Paused — select Continue to append more captions later")

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

    def _capture_behind_app(self):
        """Screenshot of the monitor the app is on, with all of the app's windows left out."""
        windows = [self.root, *(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))]
        image = screenshot.capture_behind(windows, anchor=self.root)
        if self.current_conversation is None:
            raise RuntimeError("Select a conversation before taking a screenshot.")
        return screenshot.save(image, self.current_conversation.screenshots_dir)

    @staticmethod
    def _power_choices() -> list[str]:
        return ["Keep ChatGPT's", *chatgpt.POWER_LEVELS]

    def _save_summary_power(self) -> None:
        self.settings.summary_chatgpt_power = self._power_choices().index(self.summary_power_var.get()) - 1

    def _summary_power(self) -> int | None:
        """Summary tab's Power level, or None to leave ChatGPT's slider alone."""
        self._save_summary_power()
        level = self.settings.summary_chatgpt_power
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

    def _summary_max_lines(self) -> int:
        """Summary tab's caption-line limit; 0 means the whole transcript."""
        try:
            value = max(0, int(self.summary_max_lines_var.get().strip()))
        except ValueError:
            value = self.settings.summary_max_lines
        self.settings.summary_max_lines = value
        self.summary_max_lines_var.set(str(value))
        return value

    def summarize_with_chatgpt(self) -> None:
        """Summarize the selected conversation, whether it is recording or paused."""
        tfile = self.session_file
        if tfile is None or self._sending_to_chatgpt:
            return
        if not tfile.has_text:
            messagebox.showinfo("Summarize", "No captions have been saved for this session yet.")
            return
        try:
            upload = tfile.snapshot(self._summary_max_lines())
            version = prompt_versions.resolve(self.prompt_var.get())
            if version is None:
                raise chatgpt.ChatGPTError(f"No summary prompt found in {prompt_versions.SUMMARY_DIR}.")
            prompt = chatgpt.build_prompt(upload, tfile.source, tfile.started, prompt_versions.path_for(version))
        except (OSError, chatgpt.ChatGPTError) as exc:
            messagebox.showerror("Summarize", str(exc))
            return
        # Same ChatGPT conversation as the Chat tab; "New conversation" there also applies here.
        new_chat = self.new_chat_var.get() or self.chat.wants_new_conversation
        power = self._summary_power()
        self._run_chatgpt(
            lambda status: chatgpt.ask(
                prompt,
                upload,
                status,
                new_chat=new_chat,
                tab_name=f"{chatgpt.APP_TAB}-{tfile.conversation_id}",
                power=power,
            ),
            lambda answer: self._summary_received(tfile, answer, new_chat),
            lambda error: messagebox.showerror("Summarize with ChatGPT", error),
        )

    def _summary_received(self, tfile: ConversationTranscript, answer: str, started_new_chat: bool) -> None:
        self.chat.add_summary_exchange(tfile.path.name, answer, started_new_chat)
        saved = self._save_summary(tfile, answer)
        latest = self.store.latest_summary(tfile.conversation_id)
        if latest and tfile.conversation_id == self._conversation_id():
            self._refresh_summary_history(select_id=latest.id)
            self.tabs.select(self._summary_tab)
        self.status_var.set("ChatGPT: summary received" + (f" — saved to {saved.name}" if saved else ""))

    def _save_summary(self, tfile: ConversationTranscript, answer: str):
        path = tfile.folder / "summaries.md"
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
        self.summary_tree.selection_remove(*self.summary_tree.selection())
        markdown_html.show(self.summary, "")

    # ------------------------------------------------------------ event pump

    def _poll(self) -> None:
        if self._closing:  # widgets are being destroyed; late events are dropped
            return
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

    def _handle(self, tfile: ConversationTranscript, event: Event) -> None:
        if event.kind == "status":
            self.status_var.set(event.text)
        elif event.kind == "ready":
            self.status_var.set(f"{event.text} — saving to {tfile.path.name}")
        elif event.kind == "partial":
            if tfile.conversation_id == self._conversation_id():
                self._set_transcript_partial(event.text)
            self.overlay.set_partial(event.text)
        elif event.kind == "final":
            tfile.write(event.text, event.end_of_utterance)
            if tfile.conversation_id == self._conversation_id():
                self._append_transcript(event.text, event.end_of_utterance)
            self.overlay.add_final(event.text)
        elif event.kind == "error":
            self.status_var.set("Error")
            messagebox.showerror("Real-time Caption", event.text)
            if self.transcriber:
                self.stop()
        elif event.kind == "stopped":
            self._close_session_file(tfile)
            if tfile is self._capture_file:
                self._set_transcript_partial("")
                self._finish_stop()

    # ------------------------------------------------------------- transcript

    def _set_transcript_partial(self, text: str) -> None:
        t = self.transcript
        t.configure(state="normal")
        ranges = t.tag_ranges("partial")
        if ranges:
            t.delete(ranges[0], ranges[-1])
        if text:
            if not self._line_open:
                t.insert("end", datetime.now().strftime("[%Y-%m-%d %H:%M:%S] "), "time")
                self._line_open = True
            t.insert("end", text, "partial")
        t.configure(state="disabled")
        t.see("end")

    def _append_transcript(self, text: str, end_of_utterance: bool) -> None:
        self._set_transcript_partial("")
        t = self.transcript
        t.configure(state="normal")
        if not self._line_open:
            t.insert("end", datetime.now().strftime("[%Y-%m-%d %H:%M:%S] "), "time")
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

    def save_settings(self, notify: bool = True) -> None:
        self._summary_max_lines()
        self._save_summary_power()
        self.settings.summary_new_chat = self.new_chat_var.get()
        self.chat.persist_settings()
        self.vocab.persist_settings()
        self.settings.show_overlay = self.overlay_var.get()
        self.settings.model = self.model_var.get().strip() or self.settings.model
        self.settings.compute = self.compute_var.get()
        if self.device_var.get():
            self.settings.device_label = self.device_var.get()
        self.settings.save()
        if notify:
            self.status_var.set("Settings saved")

    def on_close(self) -> None:
        self.save_settings(notify=False)
        self.stop()
        self._closing = True
        for tfile in list(self._open_files):
            self._close_session_file(tfile)
        self.root.destroy()
