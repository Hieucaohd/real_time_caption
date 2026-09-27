"""Chat tab: ask ChatGPT free-form questions about the running caption file.

Your messages appear as blue bubbles on the right, ChatGPT's answers (rendered
markdown) as grey bubbles on the left. Each message re-attaches the latest caption
file so ChatGPT always sees what has been said so far.

The chat shares the app's ChatGPT tab and conversation with "Summarize", so you can
ask follow-up questions about a summary; summaries are mirrored into this view too.
"""

from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import chatgpt, markdown_html, screenshot
from .transcript_file import TRANSCRIPTS_DIR, TranscriptFile

log = logging.getLogger(__name__)

# (job(status_callback) -> answer, on_answer(answer), on_error(message)) -> started?
RunChatGPT = Callable[[Callable[[Callable[[str], None]], str], Callable[[str], None], Callable[[str], None]], bool]


class ChatPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        get_session_file: Callable[[], TranscriptFile | None],
        get_max_lines: Callable[[], int],
        get_power: Callable[[], int | None],
        run_chatgpt: RunChatGPT,
        capture_screen: Callable[[], Path],
        screenshot_enabled: bool,
        on_screenshot_toggled: Callable[[bool], None],
    ):
        super().__init__(master)
        self._get_session_file = get_session_file
        self._get_max_lines = get_max_lines
        self._get_power = get_power
        self._run_chatgpt = run_chatgpt
        self._capture_screen = capture_screen
        # False = continue the conversation open in the app's ChatGPT tab (shared with Summarize).
        self._new_conversation = False
        self._log_path: Path | None = None
        self._busy = False
        # Rendered conversation: (role, header, body html); role is "user", "bot" or "pending".
        self._messages: list[tuple[str, str, str]] = []

        bar = ttk.Frame(self, padding=(0, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="New conversation", command=self.new_conversation).pack(side="left", padx=4)
        self.attach_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar, text="Attach the running caption file", variable=self.attach_var, command=self.refresh_state
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

        # The message box is packed at the bottom *before* the history, so it always keeps its
        # space; otherwise the expanding history would push it out of a short window.
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

    # ------------------------------------------------------------------ state

    def set_busy(self, busy: bool) -> None:
        """Called by the app while any ChatGPT request (chat or summary) is running."""
        self._busy = busy
        self.refresh_state()

    def refresh_state(self) -> None:
        tfile = self._get_session_file()
        if not self.attach_var.get():
            self.hint_var.set("Only your message will be sent")
        elif tfile is None:
            self.hint_var.set("No source running — start one to attach its caption file")
        else:
            self.hint_var.set(f"Attaching {tfile.path.name}")
        self.send_btn.configure(state="disabled" if self._busy else "normal")

    # ------------------------------------------------------------------- send

    def _on_return(self, event: tk.Event):
        if event.state & 0x0001:  # Shift held: newline
            return None
        self.send()
        return "break"

    def send(self) -> None:
        message = self.entry.get("1.0", "end").strip()
        if not message or self._busy:
            return
        tfile = self._get_session_file() if self.attach_var.get() else None
        if self.attach_var.get():
            if tfile is None:
                messagebox.showinfo(
                    "Chat", "No source is running. Start one, or untick 'Attach the running caption file'."
                )
                return
            if not tfile.has_text:
                messagebox.showinfo("Chat", "No captions have been saved for this session yet.")
                return
            try:
                file_path = tfile.snapshot(self._get_max_lines())
                prompt = chatgpt.build_prompt(
                    file_path, tfile.source, tfile.started, chatgpt.CHAT_PROMPT_PATH, message
                )
            except (OSError, chatgpt.ChatGPTError) as exc:
                messagebox.showerror("Chat", str(exc))
                return
        else:
            file_path = None
            prompt = message

        shot = None
        if self.screenshot_var.get():
            try:
                shot = self._capture_screen()
            except Exception as exc:  # noqa: BLE001
                log.exception("Screenshot failed")
                messagebox.showerror("Chat", f"Could not take the screenshot:\n{exc}")
                return

        attachments = [p for p in (file_path, shot) if p]
        new_chat = self._new_conversation
        power = self._get_power()

        def job(status: Callable[[str], None]) -> str:
            return chatgpt.ask(prompt, attachments, status, new_chat=new_chat, power=power)

        if not self._run_chatgpt(job, self._on_answer, self._on_error):
            return
        self.entry.delete("1.0", "end")
        attached = f"  ·  attached: {', '.join(p.name for p in attachments)}" if attachments else ""
        self._add_message("user", f"You · {datetime.now():%H:%M:%S}{attached}", message, image=shot)
        self._set_pending("ChatGPT is thinking…")

    def _on_answer(self, answer: str) -> None:
        self._new_conversation = False
        self._set_pending(None)
        self._add_message("bot", f"ChatGPT · {datetime.now():%H:%M:%S}", answer, markdown=True)

    def _on_error(self, error: str) -> None:
        self._set_pending(None)
        self._add_message("bot", f"Error · {datetime.now():%H:%M:%S}", error)
        messagebox.showerror("Chat with ChatGPT", error)

    def new_conversation(self) -> None:
        """Clear the view; the next message (chat or Summarize) opens a new ChatGPT chat."""
        if self._busy:
            return
        self._new_conversation = True
        self._clear()

    @property
    def wants_new_conversation(self) -> bool:
        return self._new_conversation

    def add_summary_exchange(self, file_name: str, answer: str, started_new_chat: bool) -> None:
        """Mirror a Summarize request/answer, which happened in the same ChatGPT conversation."""
        if started_new_chat:
            self._clear()
        self._new_conversation = False
        self._add_message("user", f"You · {datetime.now():%H:%M:%S}  ·  📎 {file_name}", "🧾 Summarize this caption file")
        self._add_message("bot", f"ChatGPT · {datetime.now():%H:%M:%S}", answer, markdown=True)

    def _clear(self) -> None:
        self._log_path = None
        self._messages.clear()
        self._render()

    # ---------------------------------------------------------------- display

    def _add_message(
        self, role: str, header: str, body: str, markdown: bool = False, image: Path | None = None
    ) -> None:
        body_html = markdown_html.to_html(body) if markdown else markdown_html.plain_to_html(body)
        if image is not None:
            body_html += f"<div class='shot'><img src='{screenshot.thumbnail_data_uri(image)}'></div>"
        self._messages.append((role, header, body_html))
        self._render()
        self._log(header, body + (f"\n\n![screenshot]({image.as_uri()})" if image else ""))

    def _set_pending(self, text: str | None) -> None:
        self._messages = [m for m in self._messages if m[0] != "pending"]
        if text:
            self._messages.append(("pending", "", markdown_html.plain_to_html(text)))
        self._render()

    def _render(self) -> None:
        """Your messages on the right (blue), ChatGPT's on the left (grey)."""
        parts = []
        for role, header, body in self._messages:
            if role == "pending":
                parts.append(f"<div class='pending'>{body}</div>")
                continue
            meta_class = "meta meta-right" if role == "user" else "meta"
            parts.append(f"<div class='{meta_class}'>{markdown_html.plain_to_html(header)}</div>")
            parts.append(f"<div class='{role}'>{body}</div>")
        markdown_html.show(self.history, "".join(parts), scroll_to_end=True)

    def _log(self, header: str, body: str) -> None:
        """Keep a markdown copy of the conversation next to the caption files."""
        try:
            if self._log_path is None:
                TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
                self._log_path = TRANSCRIPTS_DIR / f"chat_{datetime.now():%Y-%m-%d_%H-%M-%S}.md"
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"**{header}**\n\n{body.strip()}\n\n")
        except OSError:
            log.exception("Could not write the chat log")
