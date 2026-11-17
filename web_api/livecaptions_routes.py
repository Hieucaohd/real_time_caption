from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

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
    return request.app.state.service.captions_preferences.read()


@router.put('/settings')
def apply(body: Preferences, request: Request):
    service = request.app.state.service
    result = service.captions_preferences.save(body.model_dump())
    if result['ready']:
        with service.lock:
            service.capture['warning'] = ''
            if service.capture['error'].startswith('Không áp dụng được cài đặt Live Captions:'):
                service.capture['error'] = ''
    return result
