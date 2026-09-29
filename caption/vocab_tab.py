"""New words tab: paste English words met while watching, get them translated by ChatGPT
and saved to Voca (https://voca-zeta-five.vercel.app) with the caption line as context.

Laid out like the Chat tab: your words on the right, word cards on the left. It uses its
own ChatGPT conversation so the JSON answers don't clutter the summary/chat conversation.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable

from . import chatgpt, markdown_html, vocab
from .settings import Settings
from .conversations import ConversationTranscript
from .voca_client import VocaClient, VocaError

log = logging.getLogger(__name__)

VOCAB_TAB = "real-time-caption-vocab"  # the ChatGPT tab (window.name) used for translations
VOCA_URL = "https://voca-zeta-five.vercel.app/"
DEFAULT_COLLECTION = "Default (chosen in Voca)"
RunChatGPT = Callable[[Callable, Callable, Callable], bool]


class VocabPanel(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        settings: Settings,
        get_session_file: Callable[[], ConversationTranscript | None],
        run_chatgpt: RunChatGPT,
        on_save_settings: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self.settings = settings
        self._get_session_file = get_session_file
        self._run_chatgpt = run_chatgpt
        self._on_save_settings = on_save_settings or self._save_settings_here
        self._busy = False
        self._new_conversation = False
        self._messages: list[tuple[str, str, str]] = []  # (role, header, body html)
        self._collections: dict[str, str] = {DEFAULT_COLLECTION: ""}  # name -> id
        self._results: "queue.Queue[Callable[[], None]]" = queue.Queue()

        # --- Voca connection row
        voca_row = ttk.Frame(self, padding=(0, 4))
        voca_row.pack(fill="x")
        ttk.Label(voca_row, text="Voca API key").pack(side="left", padx=(4, 4))
        self.key_var = tk.StringVar(value=settings.voca_api_key)
        ttk.Entry(voca_row, textvariable=self.key_var, show="•", width=28).pack(side="left")
        ttk.Button(voca_row, text="Save key", command=self._save_key).pack(side="left", padx=4)
        ttk.Label(voca_row, text="Save to").pack(side="left", padx=(12, 4))
        self.collection_var = tk.StringVar(value=DEFAULT_COLLECTION)
        self.collection_box = ttk.Combobox(
            voca_row, textvariable=self.collection_var, state="readonly", width=24, values=[DEFAULT_COLLECTION]
        )
        self.collection_box.pack(side="left")
        self.collection_box.bind("<<ComboboxSelected>>", lambda _e: self._collection_chosen())
        ttk.Button(voca_row, text="↻", width=3, command=self.load_collections).pack(side="left", padx=2)
        ttk.Button(voca_row, text="Open Voca", command=lambda: webbrowser.open(VOCA_URL)).pack(side="left", padx=6)

        bar = ttk.Frame(self, padding=(0, 2))
        bar.pack(fill="x")
        ttk.Button(bar, text="New ChatGPT chat", command=self.new_conversation).pack(side="left", padx=4)
        ttk.Label(bar, text="Power").pack(side="left", padx=(10, 4))
        level = settings.vocab_chatgpt_power
        level = level if -1 <= level < len(chatgpt.POWER_LEVELS) else -1
        self.power_var = tk.StringVar(value=self._power_choices()[level + 1])
        self.power_box = ttk.Combobox(
            bar, textvariable=self.power_var, values=self._power_choices(), state="readonly", width=15
        )
        self.power_box.pack(side="left", padx=(0, 8))
        self.power_box.bind("<<ComboboxSelected>>", lambda _e: self._save_power())
        self.hint_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.hint_var, foreground="#777777").pack(side="left", padx=8)
        ttk.Button(bar, text="Save settings", command=self._on_save_settings).pack(side="right", padx=4)

        # Input first (bottom) so the expanding history can't push it out of view.
        ttk.Label(
            self, text="One word or phrase per line · Enter to send · Shift+Enter for a new line", foreground="#999999"
        ).pack(side="bottom", anchor="w")
        entry_row = ttk.Frame(self, padding=(0, 6, 0, 0))
        entry_row.pack(side="bottom", fill="x")
        self.entry = tk.Text(entry_row, height=2, wrap="word", font=("Segoe UI", 11), padx=6, pady=4)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self._on_return)
        self.send_btn = ttk.Button(entry_row, text="Translate & save ➤", command=self.send)
        self.send_btn.pack(side="left", padx=(6, 0), fill="y")

        self.history = markdown_html.make_view(self)
        self.history.pack(fill="both", expand=True)

        self.refresh_state()
        if settings.voca_api_key:
            self.after(500, self.load_collections)
        self.after(100, self._drain_results)

    # ------------------------------------------------------------------ state

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_state()

    def refresh_state(self) -> None:
        tfile = self._get_session_file()
        if not self.settings.voca_api_key:
            self.hint_var.set("Add your Voca API key (Voca → Cài đặt → Ứng dụng kết nối) to save words")
        elif tfile is None:
            self.hint_var.set("No conversation selected — words are saved without a caption sentence")
        else:
            self.hint_var.set("Context sentences come from the selected conversation")
        self.send_btn.configure(state="disabled" if self._busy else "normal")

    @staticmethod
    def _power_choices() -> list[str]:
        return ["Keep ChatGPT's", *chatgpt.POWER_LEVELS]

    def _save_power(self) -> None:
        self.settings.vocab_chatgpt_power = self._power_choices().index(self.power_var.get()) - 1

    def _power(self) -> int | None:
        self._save_power()
        level = self.settings.vocab_chatgpt_power
        return level if 0 <= level < len(chatgpt.POWER_LEVELS) else None

    def persist_settings(self) -> None:
        self._save_power()
        self.settings.voca_api_key = self.key_var.get().strip()
        self.settings.voca_collection_id = self._collections.get(self.collection_var.get(), "")

    def _save_settings_here(self) -> None:
        self.persist_settings()
        self.settings.save()

    def _save_key(self) -> None:
        self.settings.voca_api_key = self.key_var.get().strip()
        self.settings.save()
        self.refresh_state()
        if self.settings.voca_api_key:
            self.load_collections()

    def _collection_chosen(self) -> None:
        self.settings.voca_collection_id = self._collections.get(self.collection_var.get(), "")
        self.settings.save()

    def load_collections(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            return

        def fetch():
            try:
                items = VocaClient(api_key=key, max_retries=1).collections()
            except VocaError as err:
                return lambda: self.hint_var.set(f"Voca: {_voca_message(err)}")
            return lambda: self._show_collections(items)

        threading.Thread(target=lambda: self._results.put(fetch()), daemon=True).start()

    def _show_collections(self, items: list[dict]) -> None:
        self._collections = {DEFAULT_COLLECTION: ""}
        by_id = {c["id"]: c for c in items}
        for c in items:  # parents come before children; indent sub-collections
            depth, parent = 0, c.get("parent_id")
            while parent in by_id and depth < 5:
                depth, parent = depth + 1, by_id[parent].get("parent_id")
            label = "    " * depth + c["name"] + (" (shared)" if c.get("role") != "owner" else "")
            self._collections[label] = c["id"]
        self.collection_box["values"] = list(self._collections)
        chosen = next((n for n, i in self._collections.items() if i == self.settings.voca_collection_id), None)
        self.collection_var.set(chosen or DEFAULT_COLLECTION)
        self.refresh_state()

    def _drain_results(self) -> None:
        try:
            while True:
                self._results.get_nowait()()
        except queue.Empty:
            pass
        self.after(150, self._drain_results)

    # ------------------------------------------------------------------- send

    def _on_return(self, event: tk.Event):
        if event.state & 0x0001:  # Shift: newline
            return None
        self.send()
        return "break"

    def send(self) -> None:
        text = self.entry.get("1.0", "end")
        words = [vocab.NewWord(w) for w in vocab.parse_input(text)]
        if not words or self._busy:
            return
        if len(words) > 100:
            messagebox.showinfo("New words", "Please send at most 100 words at a time.")
            return
        tfile = self._get_session_file()
        for w in words:
            w.sentence, w.time = vocab.find_context(tfile.path if tfile else None, w.word)
        try:
            prompt = vocab.build_prompt(words)
        except vocab.VocabError as exc:
            messagebox.showerror("New words", str(exc))
            return

        api_key = self.settings.voca_api_key
        collection_id = self.settings.voca_collection_id
        started = tfile.started if tfile else datetime.now()
        source_title = f"Real-time caption · {started:%Y-%m-%d %H:%M}"
        new_chat, power = self._new_conversation, self._power()

        def job(status: Callable[[str], None]) -> list[vocab.NewWord]:
            answer = chatgpt.ask(prompt, None, status, new_chat=new_chat, tab_name=VOCAB_TAB, power=power)
            try:
                vocab.parse_answer(answer, words)
            except vocab.VocabError as exc:
                raise chatgpt.ChatGPTError(f"{exc}\n\nChatGPT answered:\n{answer[:1500]}") from exc
            if api_key:
                status("Saving to Voca…")
                _save_to_voca(api_key, collection_id, words, source_title)
            return words

        if not self._run_chatgpt(job, self._on_done, self._on_error):
            return
        self.entry.delete("1.0", "end")
        listing = "\n".join(w.word + (f"   [{w.time}] {w.sentence}" if w.sentence else "") for w in words)
        self._add("user", f"You · {datetime.now():%H:%M:%S}", markdown_html.plain_to_html(listing))
        self._set_pending("ChatGPT is translating…")

    def _on_done(self, words: list[vocab.NewWord]) -> None:
        self._new_conversation = False
        self._set_pending(None)
        saved = sum(w.voca_status in ("created", "updated") for w in words)
        header = f"ChatGPT · {datetime.now():%H:%M:%S}" + (f" · {saved} saved to Voca" if saved else "")
        self._add("bot", header, "".join(vocab.card_html(w) for w in words))

    def _on_error(self, error: str) -> None:
        self._set_pending(None)
        self._add("bot", f"Error · {datetime.now():%H:%M:%S}", markdown_html.plain_to_html(error))
        messagebox.showerror("New words", error.split("\n\nChatGPT answered:")[0])

    def new_conversation(self) -> None:
        if self._busy:
            return
        self._new_conversation = True
        self._messages.clear()
        self._render()

    # ---------------------------------------------------------------- display

    def _add(self, role: str, header: str, body_html: str) -> None:
        self._messages.append((role, header, body_html))
        self._render()

    def _set_pending(self, text: str | None) -> None:
        self._messages = [m for m in self._messages if m[0] != "pending"]
        if text:
            self._messages.append(("pending", "", markdown_html.plain_to_html(text)))
        self._render()

    def _render(self) -> None:
        parts = []
        for role, header, body in self._messages:
            if role == "pending":
                parts.append(f"<div class='pending'>{body}</div>")
                continue
            meta = "meta meta-right" if role == "user" else "meta"
            parts.append(f"<div class='{meta}'>{markdown_html.plain_to_html(header)}</div><div class='{role}'>{body}</div>")
        markdown_html.show(self.history, "".join(parts), scroll_to_end=True)


def _voca_message(err: VocaError) -> str:
    if err.status == 401:
        return "the API key is invalid or revoked — create a new one in Voca → Cài đặt → Ứng dụng kết nối"
    if err.status == 0:
        return f"cannot reach Voca ({err.message})"
    return f"{err.message} ({err.status} {err.code})"


def _save_to_voca(api_key: str, collection_id: str, words: list[vocab.NewWord], source_title: str) -> None:
    """Batch-upsert the words; per-word outcome goes into ``voca_status``/``voca_error``."""
    defaults: dict = {"source": vocab.VOCA_SOURCE}
    if collection_id:
        defaults["collection_id"] = collection_id
    try:
        report = VocaClient(api_key=api_key).add_words(vocab.voca_items(words, source_title), **defaults)
    except VocaError as err:
        log.warning("Voca rejected the words: %s", err)
        for w in words:
            w.voca_status, w.voca_error = "failed", _voca_message(err)
        return
    for result in report["results"]:
        w = words[result["index"]]
        w.voca_status = result.get("status", "")
        if w.voca_status == "failed":
            w.voca_error = (result.get("error") or {}).get("message", "rejected by Voca")
