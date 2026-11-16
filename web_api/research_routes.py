from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .server_math import browser_markdown
from .response_source import ResponseSourceInput

router = APIRouter(prefix='/api/conversations/{cid}/research')
preferences_router = APIRouter(prefix='/api/research')
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]


class RootInput(BaseModel):
    message_id: int


class BranchConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected_tab: str | None = Field(default=None, max_length=500)
    power: int | None = Field(default=None, ge=-1, le=2)
    attach_transcript: bool | None = None
    max_lines: int | None = Field(default=None, ge=0, le=1_000_000)
    screenshot: bool | None = None
    prompt: str | None = Field(default=None, max_length=500)
    attach_history: bool | None = None
    ancestor_depth: int | None = Field(default=None, ge=-1, le=10000, strict=True)
    new_chat: bool | None = None
    tab_mode: Literal['existing', 'new', 'branch'] | None = None


@preferences_router.get('/defaults')
def defaults(request: Request):
    return request.app.state.service.research.store.defaults()


@preferences_router.put('/defaults')
def save_defaults(body: BranchConfig, request: Request):
    return request.app.state.service.research.store.save_defaults(body.model_dump(exclude_none=True))


class NodePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    understood: bool | None = None


class RetryInput(BaseModel):
    selected_tab: str | None = Field(default=None, max_length=500)

class VisibilityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hidden: bool


class GraphPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)

class ReadingMark(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0, le=100_000_000)
    end: int = Field(ge=1, le=100_000_000)
    text: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode='after')
    def valid_range(self):
        if self.end <= self.start:
            raise ValueError('The reading mark must have a nonempty range.')
        return self


class ReadingMarkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mark: ReadingMark | None


class ChildrenInput(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=20)
    excerpt: str = Field(default='', max_length=20_000)
    config: BranchConfig = Field(default_factory=BranchConfig)
    files: list[str] = Field(default_factory=list, max_length=20)
    parent_reading_mark: ReadingMark | None = None

    # The visible Selection string and the DOM Range text are different for KaTeX
    # (hidden MathML/TeX) and block boundaries. Keep both; their strings need not match.


class GraphCamera(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=-10_000_000, le=10_000_000)
    y: float = Field(ge=-10_000_000, le=10_000_000)
    zoom: float = Field(ge=0.1, le=4)


class GraphLayoutPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    positions: dict[str, GraphPoint] | None = Field(default=None, max_length=10000)
    camera: GraphCamera | None = None
    collapsed: list[str] | None = Field(default=None, max_length=10000)
    snap: bool | None = None
    wheel_zoom: bool | None = None
    mode: Literal['select', 'hand'] | None = None
    move_subtree: bool | None = None


def research(request, cid):
    request.app.state.service.conversation(cid)
    return request.app.state.service.research


@router.get('')
def nodes(cid: str, request: Request):
    rows = research(request, cid).store.list(cid)
    return [{k: v for k, v in row.items() if k not in ('answer', 'partial', 'config', 'reading_mark', 'excerpt_mark')} for row in rows]


@router.post('/import')
def import_answer(cid: str, body: RootInput, request: Request):
    return research(request, cid).store.import_answer(cid, body.message_id)


@router.get('/{nid}/layout')
def graph_layout(cid: str, nid: str, request: Request):
    return research(request, cid).store.graph_layout(cid, nid)


@router.patch('/{nid}/layout')
def save_graph_layout(cid: str, nid: str, body: GraphLayoutPatch, request: Request):
    return research(request, cid).store.save_graph_layout(cid, nid, body.model_dump(exclude_none=True))


@router.get('/{nid}')
def node(cid: str, nid: str, request: Request):
    manager = research(request, cid)
    result = manager.store.get(cid, nid)
    with manager.service.lock:
        job = manager.service.jobs.get('research-' + nid)
        if result['status'] == 'working' and job:
            result.update(partial=job['partial'], detail=job['detail'])
    result['markdown'] = browser_markdown(result['answer'])
    result['branch_marks'] = manager.store.branch_marks(cid, nid)
    return result

@router.put('/{nid}/reading-mark')
def save_reading_mark(cid: str, nid: str, body: ReadingMarkInput, request: Request):
    return research(request, cid).store.update(cid, nid, reading_mark=body.mark.model_dump() if body.mark else None)

@router.put('/{nid}/source')
def save_source(cid: str, nid: str, body: ResponseSourceInput, request: Request):
    return research(request, cid).store.update_response_source(cid, body.model_dump(), nid=nid)


@router.post('/{nid}/children')
def children(cid: str, nid: str, body: ChildrenInput, request: Request):
    return research(request, cid).children(cid, nid, body.model_dump(exclude_none=True))


@router.post('/{nid}/chatgpt-branch')
def create_chatgpt_branch(cid: str, nid: str, request: Request):
    return research(request, cid).create_chatgpt_branch(cid, nid, return_tab=request.headers.get('x-rtc-tab', ''))


@router.post('/{nid}/open-chatgpt')
def open_chatgpt_response(cid: str, nid: str, request: Request):
    from caption.chatgpt_navigation import open_response
    node = research(request, cid).store.get(cid, nid)
    if node['status'] != 'done':
        raise ValueError('Chờ câu trả lời hoàn tất trước khi mở nguồn ChatGPT.')
    return open_response(node['response_conversation_url'], node['response_position'])


@router.patch('/{nid}')
def update(cid: str, nid: str, body: NodePatch, request: Request):
    store = research(request, cid).store
    node = store.get(cid, nid)
    if body.understood and node['status'] != 'done':
        raise ValueError('Wait for the answer before marking it understood.')
    return store.update(cid, nid, **body.model_dump(exclude_none=True))


@router.post('/{nid}/cancel')
def cancel(cid: str, nid: str, request: Request):
    return research(request, cid).cancel(cid, nid)

@router.put('/{nid}/visibility')
def visibility(cid: str, nid: str, body: VisibilityInput, request: Request):
    return research(request, cid).store.set_hidden(cid, nid, body.hidden)


@router.delete('/{nid}')
def delete_node(cid: str, nid: str, request: Request):
    return research(request, cid).delete(cid, nid)


@router.post('/{nid}/retry')
def retry(cid: str, nid: str, body: RetryInput, request: Request):
    return research(request, cid).retry(cid, nid, body.selected_tab)
