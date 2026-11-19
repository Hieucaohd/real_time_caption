"""User preferences persisted to ``settings.json`` next to the app."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields

from . import defaults
from .paths import SETTINGS_PATH
from .shared_access import ProcessLock

log = logging.getLogger(__name__)
CHATGPT_PLANS = ('', 'free', 'go', 'plus', 'pro', 'business', 'enterprise', 'edu', 'other')
CHATGPT_LOGIN_STATUSES = ('', 'signed_in', 'signed_out')
CHATGPT_ATTACHMENTS_DISABLED = 'App đã tắt đính kèm file, ảnh và chụp màn hình cho ChatGPT Free. Đổi gói trong Settings để bật lại.'
CHATGPT_SIGNED_OUT = 'Các thao tác ChatGPT đang tắt. Sau khi đăng nhập trên Chrome, hãy chọn “Đã đăng nhập” trong Settings.'


@dataclass
class Settings:
    chatgpt_plan: str = field(default_factory=lambda: defaults.get('settings', 'chatgpt_plan'))
    chatgpt_login_status: str = field(default_factory=lambda: defaults.get('settings', 'chatgpt_login_status'))
    web_capture_source: str = field(default_factory=lambda: defaults.get("settings", "web_capture_source"))
    device_label: str = field(default_factory=lambda: defaults.get("settings", "device_label"))
    model: str = field(default_factory=lambda: defaults.get("settings", "model"))
    compute: str = field(default_factory=lambda: defaults.get("settings", "compute"))
    always_on_top: bool = field(default_factory=lambda: defaults.get("settings", "always_on_top"))
    show_overlay: bool = field(default_factory=lambda: defaults.get("settings", "show_overlay"))
    overlay_font_size: int = field(default_factory=lambda: defaults.get("settings", "overlay_font_size"))
    overlay_lines: int = field(default_factory=lambda: defaults.get("settings", "overlay_lines"))
    overlay_alpha: float = field(default_factory=lambda: defaults.get("settings", "overlay_alpha"))
    overlay_transparent: bool = field(default_factory=lambda: defaults.get("settings", "overlay_transparent"))
    overlay_click_through: bool = field(default_factory=lambda: defaults.get("settings", "overlay_click_through"))
    summary_new_chat: bool = field(default_factory=lambda: defaults.get("settings", "summary_new_chat"))
    summary_attach_transcript: bool = field(default_factory=lambda: defaults.get("settings", "summary_attach_transcript"))
    summary_max_lines: int = field(default_factory=lambda: defaults.get("settings", "summary_max_lines"))
    chat_max_lines: int = field(default_factory=lambda: defaults.get("settings", "chat_max_lines"))
    summary_prompt: str = field(default_factory=lambda: defaults.get("settings", "summary_prompt"))
    chat_prompt: str = field(default_factory=lambda: defaults.get("settings", "chat_prompt"))
    summary_chatgpt_power: int = field(default_factory=lambda: defaults.get("settings", "summary_chatgpt_power"))
    chat_chatgpt_power: int = field(default_factory=lambda: defaults.get("settings", "chat_chatgpt_power"))
    vocab_chatgpt_power: int = field(default_factory=lambda: defaults.get("settings", "vocab_chatgpt_power"))
    chat_attach_history: bool = field(default_factory=lambda: defaults.get("settings", "chat_attach_history"))
    chat_attach_transcript: bool = field(default_factory=lambda: defaults.get("settings", "chat_attach_transcript"))
    chat_new_chat: bool = field(default_factory=lambda: defaults.get("settings", "chat_new_chat"))
    chatgpt_selected_tab: str = field(default_factory=lambda: defaults.get("settings", "chatgpt_selected_tab"))
    chatgpt_selected_tab_title: str = field(default_factory=lambda: defaults.get("settings", "chatgpt_selected_tab_title"))
    summary_chatgpt_selected_tab: str = field(default_factory=lambda: defaults.get("settings", "summary_chatgpt_selected_tab"))
    chat_screenshot: bool = field(default_factory=lambda: defaults.get("settings", "chat_screenshot"))
    research_selected_tab: str = field(default_factory=lambda: defaults.get("settings", "research_selected_tab"))
    research_power: int = field(default_factory=lambda: defaults.get("settings", "research_power"))
    research_attach_transcript: bool = field(default_factory=lambda: defaults.get("settings", "research_attach_transcript"))
    research_max_lines: int = field(default_factory=lambda: defaults.get("settings", "research_max_lines"))
    research_screenshot: bool = field(default_factory=lambda: defaults.get("settings", "research_screenshot"))
    research_instructions: str = field(default_factory=lambda: defaults.get("settings", "research_instructions"))
    voca_api_key: str = field(default_factory=lambda: defaults.get("settings", "voca_api_key"))
    voca_collection_id: str = field(default_factory=lambda: defaults.get("settings", "voca_collection_id"))
    overlay_width: int = field(default_factory=lambda: defaults.get("settings", "overlay_width"))
    overlay_x: int = field(default_factory=lambda: defaults.get("settings", "overlay_x"))
    overlay_y: int = field(default_factory=lambda: defaults.get("settings", "overlay_y"))
    translation_engine: str = field(default_factory=lambda: defaults.get("settings", "translation_engine"))
    translation_enabled: bool = field(default_factory=lambda: defaults.get("settings", "translation_enabled"))
    translation_partial: bool = field(default_factory=lambda: defaults.get("settings", "translation_partial"))
    translation_auto_scroll: bool = field(default_factory=lambda: defaults.get("settings", "translation_auto_scroll"))
    translation_view: str = field(default_factory=lambda: defaults.get("settings", "translation_view"))
    translation_batch_size: int = field(default_factory=lambda: defaults.get("settings", "translation_batch_size"))
    translation_batch_timeout: float = field(default_factory=lambda: defaults.get("settings", "translation_batch_timeout"))
    translation_source: str = field(default_factory=lambda: defaults.get("settings", "translation_source"))
    translation_target: str = field(default_factory=lambda: defaults.get("settings", "translation_target"))
    translation_azure_key: str = field(default_factory=lambda: defaults.get("settings", "translation_azure_key"))
    translation_azure_region: str = field(default_factory=lambda: defaults.get("settings", "translation_azure_region"))
    translation_google_key: str = field(default_factory=lambda: defaults.get("settings", "translation_google_key"))
    translation_gemini_key: str = field(default_factory=lambda: defaults.get("settings", "translation_gemini_key"))
    translation_gemini_model: str = field(default_factory=lambda: defaults.get("settings", "translation_gemini_model"))
    translation_gemini_prompt: str = field(default_factory=lambda: defaults.get("settings", "translation_gemini_prompt"))
    translation_device: str = field(default_factory=lambda: defaults.get("settings", "translation_device"))

    @property
    def chatgpt_allowed(self) -> bool:
        return self.chatgpt_login_status != 'signed_out'

    def require_chatgpt(self) -> None:
        if not self.chatgpt_allowed:
            raise ValueError(CHATGPT_SIGNED_OUT)

    @property
    def chatgpt_attachments_allowed(self) -> bool:
        return self.chatgpt_allowed and self.chatgpt_plan != 'free'

    def require_chatgpt_attachments(self) -> None:
        self.require_chatgpt()
        if not self.chatgpt_attachments_allowed:
            raise ValueError(CHATGPT_ATTACHMENTS_DISABLED)

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError('settings.json must be an object')
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.exception("Ignoring unreadable settings file")
            return cls()
        # One-time migration from the former global ChatGPT settings.  Each tab can
        # diverge after this file is saved again.
        migrations = {
            'chatgpt_power': ('summary_chatgpt_power', 'chat_chatgpt_power', 'vocab_chatgpt_power'),
            'chatgpt_max_lines': ('summary_max_lines', 'chat_max_lines'),
            'chatgpt_new_chat': ('summary_new_chat',),
            'chatgpt_selected_tab': ('summary_chatgpt_selected_tab',),
        }
        for legacy, targets in migrations.items():
            if legacy in data:
                for target in targets:
                    data.setdefault(target, data[legacy])
        known = {f.name for f in fields(cls)}
        result = cls(**{k: v for k, v in data.items() if k in known})
        result._baseline = asdict(result)
        return result

    def save(self) -> None:
        try:
            with ProcessLock(SETTINGS_PATH.with_suffix('.lock'), timeout=5):
                values = asdict(self)
                baseline = getattr(self, '_baseline', {})
                try:
                    latest = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    latest = {}
                latest.update({k: v for k, v in values.items() if baseline.get(k) != v or k not in baseline})
                fd, name = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                        json.dump(latest, stream, indent=2)
                    os.replace(name, SETTINGS_PATH)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
                self._baseline = values
        except OSError:
            log.exception("Could not save settings")
