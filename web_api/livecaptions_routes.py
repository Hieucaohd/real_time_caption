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
    return livecaptions_settings.apply_windows(request.app.state.service.store, body.model_dump(), save=True)
