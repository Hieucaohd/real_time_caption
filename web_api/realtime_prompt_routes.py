"""Reusable realtime prompts; conversation settings retain their own text snapshot."""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from caption import defaults

router = APIRouter(prefix='/api/realtime-prompts')


class TemplateInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)
    overwrite: bool = False


@router.get('')
def templates(request: Request):
    result = {kind: [dict(name='', text=defaults.get('realtime_chat', kind + '_prompt'))]
              for kind in ('actions', 'summary')}
    with request.app.state.service.store._connect() as db:
        for row in db.execute('SELECT kind,name,text FROM realtime_prompt_templates ORDER BY name COLLATE NOCASE'):
            result[row['kind']].append(dict(name=row['name'], text=row['text']))
    return result


@router.post('/{kind}')
def save_template(kind: Literal['actions', 'summary'], body: TemplateInput, request: Request):
    name = body.name.strip()
    if not name or not body.text.strip():
        raise ValueError('Tên mẫu và prompt không được để trống.')
    with request.app.state.service.store._connect() as db:
        if body.overwrite:
            changed = db.execute('UPDATE realtime_prompt_templates SET text=? WHERE kind=? AND name=?',
                                 (body.text, kind, name)).rowcount
            if not changed:
                raise ValueError('Mẫu prompt không còn tồn tại. Hãy lưu thành mẫu mới.')
        else:
            changed = db.execute('INSERT OR IGNORE INTO realtime_prompt_templates(kind,name,text) VALUES(?,?,?)',
                                 (kind, name, body.text)).rowcount
            if not changed:
                raise ValueError('Tên mẫu đã tồn tại. Đổi tên hoặc chọn Cập nhật mẫu.')
    return dict(name=name, text=body.text)
