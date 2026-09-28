"""Conversation-scoped ChatGPT panel with SQLite-backed local history."""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import chatgpt, markdown_html, screenshot
from .conversations import ConversationStore, ConversationTranscript

log = logging.getLogger(__name__)
RunChatGPT = Callable[[Callable[[Callable[[str], None]], str], Callable[[str], None], Callable[[str], None]], bool]


class ChatPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        get_session_file: Callable[[], ConversationTranscript | None],
        get_conversation_id: Callable[[], str | None],
        store: ConversationStore,
        get_max_lines: Callable[[], int],
        get_power: Callable[[], int | None],
        run_chatgpt: RunChatGPT,
        capture_screen: Callable[[], Path],
        screenshot_enabled: bool,
        on_screenshot_toggled: Callable[[bool], None],
    ):
        super().__init__(master)
        self._get_session_file = get_session_file
        self._get_conversation_id = get_conversation_id
        self._store = store
        self._get_max_lines = get_max_lines
        self._get_power = get_power
        self._run_chatgpt = run_chatgpt
        self._capture_screen = capture_screen
        self._new_conversation = False
        self._busy = False
        self._messages: list[tuple[str, str, str]] = []
        self._pending_conversation_id: str | None = None

        bar = ttk.Frame(self, padding=(0, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="New ChatGPT thread", command=self.new_conversation).pack(side="left", padx=4)
        self.attach_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar,
            text="Attach this conversation's context",
            variable=self.attach_var,
            command=self.refresh_state,
        ).pack(side="left", padx=8)
        self.screenshot_var = tk.BooleanVar(value=screenshot_enabled)
        ttk.Checkbutton(
            bar,
            text="Attach a screenshot of the apps behind",
            variable=self.screenshot_var,
            command=lambda: on_screenshot_toggled(self.screenshot_var.get()),
        ).pack(side="left", padx=8)
        self.hint_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.hint_var, foreground="#777777").pack(side="left", padx=4)

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
        elif not self.attach_var.get():
            self.hint_var.set("Only your message will be sent")
        else:
            suffix = " · next message starts a new thread" if self._new_conversation else ""
            self.hint_var.set("Transcript + saved chat history will be attached" + suffix)
        disabled = self._busy or self._get_conversation_id() is None
        self.send_btn.configure(state="disabled" if disabled else "normal")

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
        tfile = self._get_session_file() if self.attach_var.get() else None
        if self.attach_var.get():
            if tfile is None or not tfile.has_text:
                messagebox.showinfo("Chat", "This conversation has no saved captions yet.")
                return
            try:
                transcript = tfile.snapshot(self._get_max_lines())
                prompt = chatgpt.build_prompt(
                    transcript, tfile.source, tfile.started, chatgpt.CHAT_PROMPT_PATH, message
                )
            except (OSError, chatgpt.ChatGPTError) as exc:
                messagebox.showerror("Chat", str(exc))
                return
            history = self._store.chat_context_path(conversation_id)
        else:
            transcript = history = None
            prompt = message

        shot = None
        if self.screenshot_var.get():
            try:
                shot = self._capture_screen()
            except Exception as exc:  # noqa: BLE001
                log.exception("Screenshot failed")
                messagebox.showerror("Chat", f"Could not take the screenshot:\n{exc}")
                return

        attachments = [p for p in (transcript, history, shot) if p]
        new_chat, power = self._new_conversation, self._get_power()

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
