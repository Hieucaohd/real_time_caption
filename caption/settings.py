"""User preferences persisted to ``settings.json`` next to the app."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"


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
    chatgpt_new_chat: bool = True  # False = keep sending into the same ChatGPT conversation
    chatgpt_max_lines: int = 300  # caption lines sent to ChatGPT (newest); 0 = the whole file
    summary_prompt: str = ""  # version name in prompts/summarize/ ("" = newest)
    chatgpt_power: int = -1  # ChatGPT Power slider: -1 = leave as is, 0 Instant, 1 Medium, 2 High
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
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            log.exception("Could not save settings")
