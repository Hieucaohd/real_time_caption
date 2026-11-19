from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
import logging
from caption.settings import Settings

router = APIRouter(prefix='/api/conversations/{cid}/realtime-popup')


class OpacityInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    opacity: int = Field(ge=40, le=100)


class FontSizeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    font_size: int = Field(ge=1, le=32)


@router.put('/font-size')
def font_size(cid: str, body: FontSizeInput, request: Request):
    return request.app.state.service.realtime_popups.set_font_size(cid, body.font_size)


@router.get('')
def state(cid: str, request: Request):
    service = request.app.state.service
    return {**service.realtime_popups.state(cid), 'theme': service.store.web_theme()}


@router.post('')
def open_popup(cid: str, request: Request):
    Settings.load().require_chatgpt()
    from playwright.sync_api import Error
    try:
        return request.app.state.service.realtime_popups.open(cid, str(request.base_url), request.headers.get('X-RTC-Tab', ''))
    except (Error, OSError):
        logging.getLogger(__name__).exception('Could not open realtime popup')
        raise ValueError('Không mở được popup. Hãy kiểm tra Chrome 9222 đang chạy rồi thử lại.') from None


@router.put('')
def opacity(cid: str, body: OpacityInput, request: Request):
    request.app.state.service.conversation(cid)
    return request.app.state.service.realtime_popups.set_opacity(cid, body.opacity)


@router.delete('')
def close_popup(cid: str, request: Request):
    request.app.state.service.conversation(cid)
    request.app.state.service.realtime_popups.hide(cid)
    return {'visible': False}
