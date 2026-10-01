"""Conversation-scoped ChatGPT panel with SQLite-backed local history."""

from __future__ import annotations

import logging
import os
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import chatgpt, markdown_html, prompt_versions, screenshot
from .conversations import ConversationStore, ConversationTranscript
from .prompt_editor import PromptEditor
from .settings import Settings

log = logging.getLogger(__name__)
RunChatGPT = Callable[[Callable, Callable[[str], None], Callable[[str], None]], bool]


@dataclass
class ChatRequest:
    message: str
    image: Path | None
    conversation_id: str
    job: Callable | None = None
    failed: bool = False
    active: bool = False


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
        on_save_settings: Callable[[], None] | None = None,
        on_stop_chatgpt: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self._get_session_file = get_session_file
        self._get_conversation_id = get_conversation_id
        self._store = store
        self.settings = settings
        self._run_chatgpt = run_chatgpt
        self._capture_screen = capture_screen
        self._on_save_settings = on_save_settings or self._save_settings_here
        self._on_stop_chatgpt = on_stop_chatgpt or (lambda: None)
        self._new_conversation = False
        self._busy = False
        self._messages: list[tuple[str, str, str, str | None]] = []
        self._requests: dict[str, ChatRequest] = {}
        self._active_request_id: str | None = None
        self._render_scheduled = False
        self._pending_conversation_id: str | None = None
        self._active_job: Callable | None = None

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

        history_actions = ttk.Frame(self, padding=(4, 2))
        history_actions.pack(fill="x")
        ttk.Button(history_actions, text="Save settings", command=self._on_save_settings).pack(side="left", padx=4)
        ttk.Button(history_actions, text="↓ Bottom", command=lambda: self._scroll_history(1.0)).pack(
            side="right", padx=2
        )
        ttk.Button(history_actions, text="↑ Top", command=lambda: self._scroll_history(0.0)).pack(
            side="right", padx=2
        )

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
        self.history = markdown_html.make_view(self, self._on_history_link)
        self.history.pack(fill="both", expand=True)
        self.refresh_state()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_state()
        self._schedule_render()

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

    def _save_settings_here(self) -> None:
        self.persist_settings()
        self.settings.save()

    def _scroll_history(self, fraction: float) -> None:
        markdown_html.scroll_to(self.history, fraction)

    def load_conversation(self) -> None:
        self._messages.clear()
        self._requests.clear()
        self._active_request_id = None
        conversation_id = self._get_conversation_id()
        if conversation_id:
            conversation = self._store.get(conversation_id)
            for message in self._store.messages(conversation_id):
                image = None
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
                    else:
                        image = None
                action_id = None
                if message.role == "user" and message.kind == "chat":
                    action_id = f"chat-{message.id}"
                    self._requests[action_id] = ChatRequest(message.content, image, conversation_id)
                self._messages.append((message.role, message.header, body, action_id))
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
        if self._send_message(message):
            self.entry.delete("1.0", "end")

    def _send_message(self, message: str, reused_image: Path | None = None, reuse_image: bool = False) -> bool:
        """Send a new user turn, rebuilding transcript/history from the current settings."""
        conversation_id = self._get_conversation_id()
        if conversation_id is None:
            messagebox.showinfo("Chat", "Select or create a conversation first.")
            return False
        version = prompt_versions.resolve(self.prompt_var.get(), prompt_versions.CHAT_DIR)
        if not version:
            messagebox.showerror("Chat", f"No chat prompt found in {prompt_versions.CHAT_DIR}.")
            return False
        self.prompt_var.set(version)
        self.settings.chat_prompt = version
        template_path = prompt_versions.path_for(version, prompt_versions.CHAT_DIR)
        tfile = self._get_session_file() if self.attach_var.get() else None
        if self.attach_var.get():
            if tfile is None or not tfile.has_text:
                messagebox.showinfo("Chat", "This conversation has no saved captions yet.")
                return False
            try:
                transcript = tfile.snapshot(self._max_lines())
                prompt = chatgpt.build_prompt(
                    transcript, tfile.source, tfile.started, template_path, message
                )
            except (OSError, chatgpt.ChatGPTError) as exc:
                messagebox.showerror("Chat", str(exc))
                return False
        else:
            transcript = None
            try:
                prompt = chatgpt.build_prompt(None, "", None, template_path, message)
            except chatgpt.ChatGPTError as exc:
                messagebox.showerror("Chat", str(exc))
                return False
        history = self._store.chat_context_snapshot(conversation_id) if self.history_var.get() else None

        shot = reused_image if reuse_image else None
        if not reuse_image and self.screenshot_var.get():
            try:
                shot = self._capture_screen()
            except Exception as exc:  # noqa: BLE001
                log.exception("Screenshot failed")
                messagebox.showerror("Chat", f"Could not take the screenshot:\n{exc}")
                return False

        attachments = [p for p in (transcript, history, shot) if p]
        new_chat, power = self._new_conversation, self._power()

        def job(status: Callable[[str], None], cancel_event) -> str:
            return chatgpt.ask(
                prompt,
                attachments,
                status,
                new_chat=new_chat,
                tab_name=f"{chatgpt.APP_TAB}-{conversation_id}",
                power=power,
                cancel_event=cancel_event,
                use_current_tab=True,
            )

        self._pending_conversation_id = conversation_id
        self._active_job = job
        if not self._run_chatgpt(job, self._on_answer, self._on_error):
            self._pending_conversation_id = None
            self._active_job = None
            return False
        attached = f" · attached: {', '.join(p.name for p in attachments)}" if attachments else ""
        action_id = self._add_message(
            "user",
            f"You · {datetime.now():%H:%M:%S}{attached}",
            message,
            image=shot,
            conversation_id=conversation_id,
            render=False,
        )
        assert action_id is not None
        self._requests[action_id] = ChatRequest(
            message, shot, conversation_id, job=job, active=True
        )
        self._active_request_id = action_id
        self._set_pending("ChatGPT is thinking…")
        return True

    def _on_answer(self, answer: str) -> None:
        self._new_conversation = False
        request = self._requests.get(self._active_request_id or "")
        if request:
            request.active = False
            request.failed = False
        self._active_job = None
        self._set_pending(None)
        self._add_message(
            "bot",
            f"ChatGPT · {datetime.now():%H:%M:%S}",
            answer,
            markdown=True,
            conversation_id=self._pending_conversation_id,
        )
        self._active_request_id = None
        self._pending_conversation_id = None

    def _on_error(self, error: str) -> None:
        request = self._requests.get(self._active_request_id or "")
        if request:
            request.active = False
            request.failed = True
            request.job = self._active_job
        self._active_job = None
        self._set_pending(None)
        self._add_message(
            "bot",
            f"Error · {datetime.now():%H:%M:%S}",
            error,
            conversation_id=self._pending_conversation_id,
        )
        self._active_request_id = None
        self._pending_conversation_id = None
        if error != chatgpt.CANCELLED_MESSAGE:
            messagebox.showerror("Chat with ChatGPT", error)

    def retry(self, request_id: str) -> None:
        if self._busy:
            return
        request = self._requests.get(request_id)
        if request is None:
            return
        if request.failed and request.job:
            self._pending_conversation_id = request.conversation_id
            self._active_job = request.job
            request.active = True
            self._active_request_id = request_id
            if not self._run_chatgpt(request.job, self._on_answer, self._on_error):
                request.active = False
                self._active_request_id = None
                self._active_job = None
                return
            self._set_pending("Retrying with ChatGPT…")
            return

        # A successful turn is sent as a new turn. The message and screenshot are
        # reused, while transcript/history are rebuilt from the current settings.
        self._send_message(request.message, request.image, reuse_image=True)

    def _on_history_link(self, url: str) -> None:
        if url.startswith("rtc-chat-retry:"):
            request_id = url.removeprefix("rtc-chat-retry:")
            self.after_idle(lambda request_id=request_id: self.retry(request_id))
        elif url.startswith("rtc-chat-stop:"):
            request_id = url.removeprefix("rtc-chat-stop:")
            if request_id == self._active_request_id:
                self.after_idle(self._on_stop_chatgpt)
        else:
            webbrowser.open(url)

    def new_conversation(self) -> None:
        if not self._busy:
            self._new_conversation = True
            self.refresh_state()

    @property
    def wants_new_conversation(self) -> bool:
        return self._new_conversation

    def add_summary_exchange(self, file_name: str | None, answer: str, started_new_chat: bool) -> None:
        self._new_conversation = False
        self._add_message(
            "user",
            f"You · {datetime.now():%H:%M:%S}" + (f" · 📎 {file_name}" if file_name else ""),
            "🧾 Summarize this conversation" if file_name else "🧾 Summarize the current ChatGPT conversation",
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
        render: bool = True,
    ) -> str | None:
        conversation_id = conversation_id or self._get_conversation_id()
        stored_id = None
        if conversation_id:
            stored_id = self._store.add_message(
                conversation_id, role, header, body, kind=kind, attachment=image
            )
        if conversation_id != self._get_conversation_id():
            return None
        body_html = markdown_html.to_html(body) if markdown else markdown_html.plain_to_html(body)
        if image is not None:
            body_html += f"<div class='shot'><img src='{screenshot.thumbnail_data_uri(image)}'></div>"
        action_id = f"chat-{stored_id}" if role == "user" and kind == "chat" and stored_id else None
        self._messages.append((role, header, body_html, action_id))
        if render:
            self._render()
        return action_id

    def _set_pending(self, value: str | None) -> None:
        self._messages = [message for message in self._messages if message[0] != "pending"]
        if value:
            self._messages.append(("pending", "", markdown_html.plain_to_html(value), None))
        self._render()

    def _render(self) -> None:
        parts = []
        for role, header, body, action_id in self._messages:
            if role == "pending":
                parts.append(f"<div class='pending'>{body}</div>")
            else:
                meta = "meta meta-right" if role == "user" else "meta"
                parts.append(f"<div class='{meta}'>{markdown_html.plain_to_html(header)}</div>")
                actions = self._request_actions(action_id) if action_id else ""
                parts.append(f"<div class='{role}'>{body}{actions}</div>")
        markdown_html.show(self.history, "".join(parts), scroll_to_end=True)

    def _schedule_render(self) -> None:
        """Coalesce state renders and never reload Tkhtml inside its own event callback."""
        if self._render_scheduled:
            return
        self._render_scheduled = True

        def render() -> None:
            self._render_scheduled = False
            if self.winfo_exists():
                self._render()

        self.after_idle(render)

    def _request_actions(self, request_id: str) -> str:
        request = self._requests.get(request_id)
        retry = (
            f"<a href='rtc-chat-retry:{request_id}'>↻ Retry</a>"
            if request and not self._busy
            else "<span class='disabled'>↻ Retry</span>"
        )
        stop = (
            f"<a href='rtc-chat-stop:{request_id}'>■ Stop</a>"
            if request and request.active and self._busy
            else "<span class='disabled'>■ Stop</span>"
        )
        return f"<div class='message-actions'>{retry}{stop}</div>"
