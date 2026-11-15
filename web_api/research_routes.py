from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .server_math import browser_markdown

router = APIRouter(prefix='/api/conversations/{cid}/research')
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
    instructions: str | None = Field(default=None, max_length=20_000)


class ChildrenInput(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=20)
    excerpt: str = Field(default='', max_length=20_000)
    config: BranchConfig = Field(default_factory=BranchConfig)
    files: list[str] = Field(default_factory=list, max_length=20)


class NodePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    understood: bool | None = None


class RetryInput(BaseModel):
    selected_tab: str | None = Field(default=None, max_length=500)


class GraphPoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


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
    return [{k: v for k, v in row.items() if k not in ('answer', 'partial', 'config')} for row in rows]


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
    return result


@router.post('/{nid}/children')
def children(cid: str, nid: str, body: ChildrenInput, request: Request):
    return research(request, cid).children(cid, nid, body.model_dump(exclude_none=True))


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


@router.post('/{nid}/retry')
def retry(cid: str, nid: str, body: RetryInput, request: Request):
    return research(request, cid).retry(cid, nid, body.selected_tab)
