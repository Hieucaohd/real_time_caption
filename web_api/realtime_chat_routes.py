from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/conversations/{cid}/realtime-chat')


class RealtimeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    selected_tab: str = Field(default='', max_length=500)
    selected_tab_title: str = Field(default='', max_length=500)
    summary_tab: str = Field(default='', max_length=500)
    summary_tab_title: str = Field(default='', max_length=500)
    interval: int = Field(default=30, ge=5, le=3600)
    prompt: str = Field(default='', max_length=200)
    actions_prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    summary_prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    actions_template: str = Field(default='', max_length=200)
    summary_template: str = Field(default='', max_length=200)
    power: int = Field(default=-1, ge=-1, le=2)
    # Missing in older settings/archives: inherit their shared Power once.
    summary_power: int | None = Field(default=None, ge=-1, le=2)
    max_lines: int = Field(default=300, ge=0, le=1_000_000)
    attach_transcript: bool = True
    attach_history: bool = False
    screenshot: bool = True
    new_chat: bool = False


class RealtimeMessage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    message: str = Field(default='', max_length=100_000)
    action_id: str = Field(default='', max_length=100)
    files: list[str] = Field(default_factory=list, max_length=20)


@router.get('')
def state(cid: str, request: Request):
    return request.app.state.service.realtime_chat.state(cid)


@router.get('/history')
def history(cid: str, request: Request, before: int = Query(gt=0)):
    service = request.app.state.service
    service.conversation(cid)
    return service.realtime_chat.store.history(cid, before)


@router.put('/settings')
def save(cid: str, config: RealtimeConfig, request: Request):
    return request.app.state.service.realtime_chat.save(cid, config.model_dump())


@router.post('/start')
def start(cid: str, config: RealtimeConfig, request: Request):
    return request.app.state.service.realtime_chat.start(cid, config.model_dump(), request.headers.get('X-RTC-Tab', ''))


@router.post('/stop')
def stop(cid: str, request: Request):
    request.app.state.service.realtime_chat.stop(cid)
    return request.app.state.service.realtime_chat.state(cid)


@router.post('/messages')
def send(cid: str, message: RealtimeMessage, request: Request):
    return request.app.state.service.realtime_chat.send(cid, message.message, message.files, message.action_id)
