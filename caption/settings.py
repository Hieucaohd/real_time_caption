"""User preferences persisted to ``settings.json`` next to the app."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields

from .paths import SETTINGS_PATH

log = logging.getLogger(__name__)


@dataclass
class Settings:
    device_label: str = ""
    model: str = "small.en"
    compute: str = "auto"
    always_on_top: bool = False  # keep the main window above other apps
    show_overlay: bool = True
    overlay_font_size: int = 22
    overlay_lines: int = 2
    overlay_alpha: float = 0.82
    overlay_transparent: bool = False  # text only, no background bar
    overlay_click_through: bool = False  # mouse passes through the overlay
    summary_new_chat: bool = True
    summary_attach_transcript: bool = True
    summary_max_lines: int = 300  # newest caption lines; 0 = the whole transcript
    chat_max_lines: int = 300
    summary_prompt: str = ""  # version name in prompts/summarize/ ("" = newest)
    chat_prompt: str = ""  # version name in prompts/chat/ ("" = newest)
    summary_chatgpt_power: int = -1
    chat_chatgpt_power: int = -1
    vocab_chatgpt_power: int = -1
    chat_attach_history: bool = False  # local history is large and normally unnecessary
    chatgpt_selected_tab: str = ""  # invisible window.name key assigned to the chosen Chrome tab
    summary_chatgpt_selected_tab: str = ""  # independently selected Chrome tab for summaries
    chat_screenshot: bool = True  # attach a screenshot of the apps behind this one to each chat message
    voca_api_key: str = ""  # personal key from Voca → Cài đặt → Ứng dụng kết nối (kept only in settings.json)
    voca_collection_id: str = ""  # "" = the app's default collection in Voca
    overlay_width: int = 0  # 0 = pick from screen size
    overlay_x: int = -1  # -1 = centre near the bottom of the screen
    overlay_y: int = -1

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.exception("Ignoring unreadable settings file")
            return cls()
        # One-time migration from the former global ChatGPT settings.  Each tab can
        # diverge after this file is saved again.
        old_power = data.get("chatgpt_power", -1)
        old_lines = data.get("chatgpt_max_lines", 300)
        data.setdefault("summary_chatgpt_power", old_power)
        data.setdefault("chat_chatgpt_power", old_power)
        data.setdefault("vocab_chatgpt_power", old_power)
        data.setdefault("summary_max_lines", old_lines)
        data.setdefault("chat_max_lines", old_lines)
        data.setdefault("summary_new_chat", data.get("chatgpt_new_chat", True))
        data.setdefault("summary_chatgpt_selected_tab", data.get("chatgpt_selected_tab", ""))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            log.exception("Could not save settings")
