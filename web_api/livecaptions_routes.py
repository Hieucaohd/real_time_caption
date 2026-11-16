from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from caption import livecaptions_settings

router = APIRouter(prefix='/api/livecaptions')


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    microphone: bool | None = None
    language: str = Field(default='', max_length=200)


@router.get('/settings')
def saved(request: Request):
    return request.app.state.service.store.livecaptions_preferences()


@router.post('/inspect')
def inspect(request: Request):
    return livecaptions_settings.read_windows(request.app.state.service.store)


@router.put('/settings')
def apply(body: Preferences, request: Request):
    service = request.app.state.service
    result = livecaptions_settings.apply_windows(service.store, body.model_dump(), save=True)
    if result['ready']:
        with service.lock:
            if service.capture['error'].startswith('Không áp dụng được cài đặt Live Captions:'):
                service.capture['error'] = ''
    return result
