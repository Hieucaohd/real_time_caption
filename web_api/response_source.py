"""Validated manual source metadata shared by Chat and research answers."""
import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResponseSourceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    response_conversation_url: str = Field(default='', max_length=2000)
    response_message_id: str = Field(default='', max_length=500)
    response_turn_id: str = Field(default='', max_length=500)

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
        return urlunsplit((url.scheme, url.netloc, url.path.rstrip('/'), '', ''))

    @field_validator('response_message_id', 'response_turn_id')
    @classmethod
    def identifier(cls, value):
        if value and not re.fullmatch(r'[A-Za-z0-9._:-]+', value):
            raise ValueError('Mã response/turn không được chứa khoảng trắng hay ký tự đặc biệt khác dấu . _ : -')
        return value
