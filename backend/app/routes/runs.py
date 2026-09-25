import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from app.approvals import MERGE_TOOL, ApprovalRefused, check_merge, load_pending
from app.auth import require_api_key
from app.ledger import Ledger
from app.routes.access import AccessResponse, resolve_access
from app.runs import RunManager
from app.trueforge import TrueForgeError

router = APIRouter(tags=["runs"], dependencies=[Depends(require_api_key)])

KEEPALIVE_SECONDS = 15
RELEASE_CACHE_SECONDS = 2.0


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


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=1, max_length=200)


class ApprovalResponse(BaseModel):
    run_id: str
    decision: Literal["approve", "reject"]
    approver: str
    decided_at: str
    status: str
    # True when this call returned an earlier decision instead of making a new one.
    replayed: bool


class ReleaseResponse(BaseModel):
    merged: bool
    merge_commit_sha: str | None
    status: str | None
    conclusion: str | None
    url: str | None


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


@router.post("/runs/{run_id}/approval", response_model=ApprovalResponse)
async def decide_approval(run_id: str, body: ApprovalRequest, request: Request) -> ApprovalResponse:
    state = request.app.state
    ledger: Ledger = state.ledger
    manager: RunManager = state.run_manager

    async with manager.approval_lock(run_id):
        await _get_run(ledger, run_id)
        prior = await ledger.accepted_approval(run_id)
        if prior is not None:
            run = await _get_run(ledger, run_id)
            return ApprovalResponse(run_id=run_id, status=run["status"], replayed=True, **_decision(prior))

        # Let queued stream writes (pending action, turn.done) land before reading the run.
        await ledger.flush()
        run = await _get_run(ledger, run_id)
        tool_name, arguments = "unknown", None
        try:
            action, tool = await load_pending(run, ledger, state.trueforge_client)
            tool_name, arguments = tool.name or "unknown", tool.arguments
            if body.decision == "approve":
                await check_merge(run, tool, state.github_client)
        except ApprovalRefused as exc:
            await ledger.record_approval(
                run_id=run_id,
                tool_name=tool_name,
                arguments=arguments,
                decision=body.decision,
                approver=body.approver,
                result=f"refused: {exc.reason}",
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason) from exc

        # The decision is durable before TrueForge hears about it.
        decision = await ledger.record_approval(
            run_id=run_id,
            tool_name=tool_name,
            arguments=arguments,
            decision=body.decision,
            approver=body.approver,
            result="accepted",
        )

        approved = body.decision == "approve"
        try:
            turn = await state.trueforge_client.resume_with_approval(
                run["session_id"], action, approved, reason=None if approved else f"Rejected by {body.approver}"
            )
        except (TrueForgeError, httpx.HTTPError) as exc:
            ledger.set_status(run_id, "resume_failed")
            await ledger.flush()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Decision recorded but TrueForge did not resume: {exc}"
            ) from exc

        base = await ledger.last_sequence(run_id)
        await ledger.begin_turn(run_id, turn.turn_id, base)
        manager.start(run_id, run["session_id"], turn.turn_id, after_sequence=base, base=base)

        return ApprovalResponse(run_id=run_id, status="running", replayed=False, **_decision(decision))


def _decision(row: dict[str, Any]) -> dict[str, Any]:
    return {"decision": row["decision"], "approver": row["approver"], "decided_at": row["decided_at"]}


@router.get("/runs/{run_id}/release", response_model=ReleaseResponse)
async def get_release(run_id: str, request: Request) -> ReleaseResponse:
    state = request.app.state
    manager: RunManager = state.run_manager
    run = await _get_run(state.ledger, run_id)

    cached = manager.release_cache.get(run_id)
    if cached is not None and cached[0] > time.monotonic():
        return ReleaseResponse(**cached[1])

    approval = await state.ledger.accepted_approval(run_id)
    if approval is None or approval["decision"] != "approve" or approval["tool_name"] != MERGE_TOOL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run has no approved merge")

    owner, repo = run["repo"].split("/", 1)
    try:
        pr = await state.github_client.get_pr(owner, repo, approval["arguments"]["pullNumber"], fresh=True)
        release: dict[str, Any] = {
            "merged": pr["merged"],
            "merge_commit_sha": pr["merge_commit_sha"] if pr["merged"] else None,
            "status": None,
            "conclusion": None,
            "url": None,
        }
        if pr["merged"]:
            access = await state.github_client.check_access(owner, repo)
            workflow = await state.github_client.latest_workflow_run(
                owner, repo, pr["merge_commit_sha"], access["default_branch"]
            )
            if workflow is not None:
                release.update(workflow)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not read the release from GitHub") from exc

    manager.release_cache[run_id] = (time.monotonic() + RELEASE_CACHE_SECONDS, release)
    return ReleaseResponse(**release)
