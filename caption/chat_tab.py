"""Conversation-scoped ChatGPT panel with SQLite-backed local history."""

from __future__ import annotations

import logging
import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import chatgpt, markdown_html, prompt_versions, screenshot
from .conversations import ConversationStore, ConversationTranscript
from .prompt_editor import PromptEditor
from .settings import Settings

log = logging.getLogger(__name__)
RunChatGPT = Callable[[Callable[[Callable[[str], None]], str], Callable[[str], None], Callable[[str], None]], bool]


class ChatPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        get_session_file: Callable[[], ConversationTranscript | None],
        get_conversation_id: Callable[[], str | None],
        store: ConversationStore,
        settings: Settings,
        run_chatgpt: RunChatGPT,
        capture_screen: Callable[[], Path],
        screenshot_enabled: bool,
        on_screenshot_toggled: Callable[[bool], None],
    ):
        super().__init__(master)
        self._get_session_file = get_session_file
        self._get_conversation_id = get_conversation_id
        self._store = store
        self.settings = settings
        self._run_chatgpt = run_chatgpt
        self._capture_screen = capture_screen
        self._new_conversation = False
        self._busy = False
        self._messages: list[tuple[str, str, str]] = []
        self._pending_conversation_id: str | None = None

        bar = ttk.Frame(self, padding=(0, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="New ChatGPT thread", command=self.new_conversation).pack(side="left", padx=4)
        ttk.Label(bar, text="Power").pack(side="left", padx=(10, 4))
        level = self.settings.chat_chatgpt_power
        level = level if -1 <= level < len(chatgpt.POWER_LEVELS) else -1
        self.power_var = tk.StringVar(value=self._power_choices()[level + 1])
        self.power_box = ttk.Combobox(
            bar, textvariable=self.power_var, values=self._power_choices(), state="readonly", width=15
        )
        self.power_box.pack(side="left", padx=(0, 8))
        self.power_box.bind("<<ComboboxSelected>>", lambda _e: self._save_power())
        ttk.Label(bar, text="Send last").pack(side="left", padx=(4, 3))
        self.max_lines_var = tk.StringVar(value=str(self.settings.chat_max_lines))
        ttk.Spinbox(
            bar, from_=0, to=100_000, increment=50, width=7, textvariable=self.max_lines_var
        ).pack(side="left")
        ttk.Label(bar, text="lines (0 = all)").pack(side="left", padx=(3, 8))

        context_bar = ttk.Frame(self, padding=(0, 1))
        context_bar.pack(fill="x")
        self.attach_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            context_bar,
            text="Attach transcript",
            variable=self.attach_var,
            command=self.refresh_state,
        ).pack(side="left", padx=8)
        self.history_var = tk.BooleanVar(value=self.settings.chat_attach_history)
        ttk.Checkbutton(
            context_bar,
            text="Attach saved chat history",
            variable=self.history_var,
            command=self._history_toggled,
        ).pack(side="left", padx=8)
        self.screenshot_var = tk.BooleanVar(value=screenshot_enabled)
        ttk.Checkbutton(
            context_bar,
            text="Attach a screenshot of the apps behind",
            variable=self.screenshot_var,
            command=lambda: on_screenshot_toggled(self.screenshot_var.get()),
        ).pack(side="left", padx=8)
        self.hint_var = tk.StringVar()
        ttk.Label(context_bar, textvariable=self.hint_var, foreground="#777777").pack(side="left", padx=4)

        prompt_bar = ttk.Frame(self, padding=(4, 4, 0, 2))
        prompt_bar.pack(fill="x")
        ttk.Label(prompt_bar, text="Chat prompt").pack(side="left", padx=(4, 6))
        self.prompt_var = tk.StringVar(
            value=prompt_versions.resolve(self.settings.chat_prompt, prompt_versions.CHAT_DIR) or ""
        )
        self.prompt_box = ttk.Combobox(
            prompt_bar,
            textvariable=self.prompt_var,
            values=prompt_versions.list_versions(prompt_versions.CHAT_DIR),
            state="readonly",
            width=28,
            postcommand=self._refresh_prompt_versions,
        )
        self.prompt_box.pack(side="left", padx=(0, 6))
        self.prompt_box.bind("<<ComboboxSelected>>", lambda _e: self._save_prompt())
        ttk.Button(prompt_bar, text="Edit / new version…", command=self._edit_prompt).pack(side="left")
        ttk.Button(prompt_bar, text="📁", width=3, command=self._open_prompt_folder).pack(side="left", padx=5)

        ttk.Label(self, text="Enter to send · Shift+Enter for a new line", foreground="#999999").pack(
            side="bottom", anchor="w"
        )
        entry_row = ttk.Frame(self, padding=(0, 6, 0, 0))
        entry_row.pack(side="bottom", fill="x")
        self.entry = tk.Text(entry_row, height=3, wrap="word", font=("Segoe UI", 11), padx=6, pady=4)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_return)
        self.send_btn = ttk.Button(entry_row, text="Send ➤", command=self.send)
        self.send_btn.pack(side="left", padx=(6, 0), fill="y")
        self.history = markdown_html.make_view(self)
        self.history.pack(fill="both", expand=True)
        self.refresh_state()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_state()

    def refresh_state(self) -> None:
        if self._get_conversation_id() is None:
            self.hint_var.set("Select or create a conversation first")
        elif not self.attach_var.get() and not self.history_var.get():
            self.hint_var.set("No text context will be attached")
        else:
            suffix = " · next message starts a new thread" if self._new_conversation else ""
            context = []
            if self.attach_var.get():
                context.append("transcript")
            if self.history_var.get():
                context.append("saved history")
            self.hint_var.set(" + ".join(context).capitalize() + " will be attached" + suffix)
        disabled = self._busy or self._get_conversation_id() is None
        self.send_btn.configure(state="disabled" if disabled else "normal")

    @staticmethod
    def _power_choices() -> list[str]:
        return ["Keep ChatGPT's", *chatgpt.POWER_LEVELS]

    def _save_power(self) -> None:
        self.settings.chat_chatgpt_power = self._power_choices().index(self.power_var.get()) - 1

    def _power(self) -> int | None:
        self._save_power()
        level = self.settings.chat_chatgpt_power
        return level if 0 <= level < len(chatgpt.POWER_LEVELS) else None

    def _max_lines(self) -> int:
        try:
            value = max(0, int(self.max_lines_var.get().strip()))
        except ValueError:
            value = self.settings.chat_max_lines
        self.settings.chat_max_lines = value
        self.max_lines_var.set(str(value))
        return value

    def _history_toggled(self) -> None:
        self.settings.chat_attach_history = self.history_var.get()
        self.refresh_state()

    def _refresh_prompt_versions(self) -> None:
        versions = prompt_versions.list_versions(prompt_versions.CHAT_DIR)
        self.prompt_box.configure(values=versions)
        chosen = prompt_versions.resolve(self.prompt_var.get(), prompt_versions.CHAT_DIR)
        self.prompt_var.set(chosen or "")
        self.settings.chat_prompt = chosen or ""

    def _save_prompt(self) -> None:
        self.settings.chat_prompt = self.prompt_var.get()

    def _edit_prompt(self) -> None:
        base = prompt_versions.resolve(self.prompt_var.get(), prompt_versions.CHAT_DIR)
        if not base:
            messagebox.showerror("Chat prompt", f"No chat prompt found in {prompt_versions.CHAT_DIR}.")
            return

        def saved(name: str) -> None:
            self._refresh_prompt_versions()
            self.prompt_var.set(name)
            self.settings.chat_prompt = name

        PromptEditor(
            self.winfo_toplevel(),
            base,
            saved,
            prompt_dir=prompt_versions.CHAT_DIR,
            prompt_name="Chat prompt",
            placeholders=prompt_versions.CHAT_PLACEHOLDERS,
            required_placeholder="{message}",
        )

    @staticmethod
    def _open_prompt_folder() -> None:
        prompt_versions.CHAT_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(prompt_versions.CHAT_DIR)  # type: ignore[attr-defined]

    def persist_settings(self) -> None:
        self._max_lines()
        self._save_power()
        self.settings.chat_attach_history = self.history_var.get()
        self._save_prompt()

    def load_conversation(self) -> None:
        self._messages.clear()
        conversation_id = self._get_conversation_id()
        if conversation_id:
            conversation = self._store.get(conversation_id)
            for message in self._store.messages(conversation_id):
                body = (
                    markdown_html.to_html(message.content)
                    if message.role == "bot"
                    else markdown_html.plain_to_html(message.content)
                )
                if message.attachment and conversation:
                    image = Path(message.attachment)
                    if not image.is_absolute():
                        image = conversation.folder / image
                    if image.exists():
                        body += f"<div class='shot'><img src='{screenshot.thumbnail_data_uri(image)}'></div>"
                self._messages.append((message.role, message.header, body))
        self._render()
        self.refresh_state()

    def _on_return(self, event: tk.Event):
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def send(self) -> None:
        message = self.entry.get("1.0", "end").strip()
        if not message or self._busy:
            return
        conversation_id = self._get_conversation_id()
        if conversation_id is None:
            messagebox.showinfo("Chat", "Select or create a conversation first.")
            return
        version = prompt_versions.resolve(self.prompt_var.get(), prompt_versions.CHAT_DIR)
        if not version:
            messagebox.showerror("Chat", f"No chat prompt found in {prompt_versions.CHAT_DIR}.")
            return
        self.prompt_var.set(version)
        self.settings.chat_prompt = version
        template_path = prompt_versions.path_for(version, prompt_versions.CHAT_DIR)
        tfile = self._get_session_file() if self.attach_var.get() else None
        if self.attach_var.get():
            if tfile is None or not tfile.has_text:
                messagebox.showinfo("Chat", "This conversation has no saved captions yet.")
                return
            try:
                transcript = tfile.snapshot(self._max_lines())
                prompt = chatgpt.build_prompt(
                    transcript, tfile.source, tfile.started, template_path, message
                )
            except (OSError, chatgpt.ChatGPTError) as exc:
                messagebox.showerror("Chat", str(exc))
                return
        else:
            transcript = None
            try:
                prompt = chatgpt.build_prompt(None, "", None, template_path, message)
            except chatgpt.ChatGPTError as exc:
                messagebox.showerror("Chat", str(exc))
                return
        history = self._store.chat_context_path(conversation_id) if self.history_var.get() else None

        shot = None
        if self.screenshot_var.get():
            try:
                shot = self._capture_screen()
            except Exception as exc:  # noqa: BLE001
                log.exception("Screenshot failed")
                messagebox.showerror("Chat", f"Could not take the screenshot:\n{exc}")
                return

        attachments = [p for p in (transcript, history, shot) if p]
        new_chat, power = self._new_conversation, self._power()

        def job(status: Callable[[str], None]) -> str:
            return chatgpt.ask(
                prompt,
                attachments,
                status,
                new_chat=new_chat,
                tab_name=f"{chatgpt.APP_TAB}-{conversation_id}",
                power=power,
            )

        self._pending_conversation_id = conversation_id
        if not self._run_chatgpt(job, self._on_answer, self._on_error):
            self._pending_conversation_id = None
            return
        self.entry.delete("1.0", "end")
        attached = f" · attached: {', '.join(p.name for p in attachments)}" if attachments else ""
        self._add_message(
            "user",
            f"You · {datetime.now():%H:%M:%S}{attached}",
            message,
            image=shot,
            conversation_id=conversation_id,
        )
        self._set_pending("ChatGPT is thinking…")

    def _on_answer(self, answer: str) -> None:
        self._new_conversation = False
        self._set_pending(None)
        self._add_message(
            "bot",
            f"ChatGPT · {datetime.now():%H:%M:%S}",
            answer,
            markdown=True,
            conversation_id=self._pending_conversation_id,
        )
        self._pending_conversation_id = None

    def _on_error(self, error: str) -> None:
        self._set_pending(None)
        self._add_message(
            "bot",
            f"Error · {datetime.now():%H:%M:%S}",
            error,
            conversation_id=self._pending_conversation_id,
        )
        self._pending_conversation_id = None
        messagebox.showerror("Chat with ChatGPT", error)

    def new_conversation(self) -> None:
        if not self._busy:
            self._new_conversation = True
            self.refresh_state()

    @property
    def wants_new_conversation(self) -> bool:
        return self._new_conversation

    def add_summary_exchange(self, file_name: str, answer: str, started_new_chat: bool) -> None:
        self._new_conversation = False
        self._add_message(
            "user",
            f"You · {datetime.now():%H:%M:%S} · 📎 {file_name}",
            "🧾 Summarize this conversation",
            kind="summary",
        )
        self._add_message(
            "bot",
            f"ChatGPT · {datetime.now():%H:%M:%S}",
            answer,
            markdown=True,
            kind="summary",
        )

    def _add_message(
        self,
        role: str,
        header: str,
        body: str,
        markdown: bool = False,
        image: Path | None = None,
        conversation_id: str | None = None,
        kind: str = "chat",
    ) -> None:
        conversation_id = conversation_id or self._get_conversation_id()
        if conversation_id:
            self._store.add_message(conversation_id, role, header, body, kind=kind, attachment=image)
        if conversation_id != self._get_conversation_id():
            return
        body_html = markdown_html.to_html(body) if markdown else markdown_html.plain_to_html(body)
        if image is not None:
            body_html += f"<div class='shot'><img src='{screenshot.thumbnail_data_uri(image)}'></div>"
        self._messages.append((role, header, body_html))
        self._render()

    def _set_pending(self, value: str | None) -> None:
        self._messages = [message for message in self._messages if message[0] != "pending"]
        if value:
            self._messages.append(("pending", "", markdown_html.plain_to_html(value)))
        self._render()

    def _render(self) -> None:
        parts = []
        for role, header, body in self._messages:
            if role == "pending":
                parts.append(f"<div class='pending'>{body}</div>")
            else:
                meta = "meta meta-right" if role == "user" else "meta"
                parts.append(f"<div class='{meta}'>{markdown_html.plain_to_html(header)}</div>")
                parts.append(f"<div class='{role}'>{body}</div>")
        markdown_html.show(self.history, "".join(parts), scroll_to_end=True)
