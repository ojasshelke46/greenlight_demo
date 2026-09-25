import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.auth import require_api_key
from app.ledger import Ledger
from app.routes.access import AccessResponse, resolve_access
from app.runs import RunManager
from app.trueforge import TrueForgeError

router = APIRouter(tags=["runs"], dependencies=[Depends(require_api_key)])

KEEPALIVE_SECONDS = 15


class CreateRunRequest(BaseModel):
    repo: str
    mode: Literal["ship", "pr_only"]


class CreateRunResponse(BaseModel):
    run_id: str
    access: AccessResponse


class RunResponse(BaseModel):
    id: str
    status: str
    repo: str
    mode: str
    via_fork: bool
    pending_action: dict[str, Any] | None


def build_prompt(repo: str, mode: str) -> str:
    return f"Check https://github.com/{repo} for vulnerable packages and fix them.\nMODE: {mode}"


async def _get_run(ledger: Ledger, run_id: str) -> dict[str, Any]:
    run = await ledger.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return run


@router.post("/runs", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateRunRequest, request: Request) -> CreateRunResponse:
    state = request.app.state
    access = await resolve_access(state.github_client, body.repo)

    if body.mode not in access.mode_options:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mode ship needs push access to {access.repo}. Use pr_only instead.",
        )

    trueforge = state.trueforge_client
    try:
        session_id = await trueforge.create_session(await trueforge.get_agent_id())
        turn = await trueforge.start_turn(session_id, build_prompt(access.repo, body.mode))
    except (TrueForgeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not start the TrueForge agent: {exc}") from exc
    turn_id = turn.turn_id

    run_id = str(uuid.uuid4())
    await state.ledger.create_run(
        run_id=run_id,
        repo=access.repo,
        mode=body.mode,
        via_fork=access.via_fork,
        session_id=session_id,
        turn_id=turn_id,
    )
    state.run_manager.start(run_id, session_id, turn_id)

    return CreateRunResponse(run_id=run_id, access=access)


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, request: Request) -> RunResponse:
    run = await _get_run(request.app.state.ledger, run_id)
    return RunResponse(**run)


@router.get("/runs/{run_id}/ledger")
async def get_ledger(run_id: str, request: Request) -> dict[str, Any]:
    trail = await request.app.state.ledger.audit_trail(run_id)
    if trail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return trail


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    after = 0
    if last_event_id:
        try:
            after = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Last-Event-ID") from exc

    run = await _get_run(request.app.state.ledger, run_id)
    manager: RunManager = request.app.state.run_manager
    await manager.ensure_pump(run)

    async def events() -> AsyncIterator[dict[str, str]]:
        async for sequence, data in manager.stream(run_id, after):
            yield {"id": str(sequence), "data": data}

    return EventSourceResponse(
        events(),
        ping=KEEPALIVE_SECONDS,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
