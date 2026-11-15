"""Validated manual source metadata shared by Chat and research answers."""
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResponseSourceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    response_conversation_url: str = Field(default='', max_length=2000)
    response_position: int | None = Field(default=None, ge=1, le=2147483647, strict=True)

    @field_validator('response_conversation_url')
    @classmethod
    def conversation_url(cls, value):
        if not value:
            return value
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname not in ('chatgpt.com', 'www.chatgpt.com', 'chat.openai.com')
                or url.username or url.password or url.port not in (None, 443)
                or not re.search(r'/c/[A-Za-z0-9_-]+/?$', url.path)):
            raise ValueError('Nhập link cuộc hội thoại ChatGPT có dạng https://chatgpt.com/.../c/...')
        return value
