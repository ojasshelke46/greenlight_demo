import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from app.approvals import (
    MERGE_TOOL,
    NEEDS_MORE_PREFIX,
    ApprovalRefused,
    approval_context,
    approvals_for_run,
    approver_decision,
    check_merge,
    load_pending,
)
from app.auth import require_api_key
from app.config import CHAT_ROLES
from app.features.fleet.models import FleetSummary, scout_board
from app.features.fleet.scan import InvalidTarget, Target, parse_target
from app.features.policy.loader import load_policy
from app.github import InvalidRepoUrl, parse_repo
from app.hooks import Deny, NeedMore, build_run_message_extras, run_approval_checks
from app.ledger import Ledger
from app.log import run_id_var
from app.orchestrator import CHILD_MODE, FIXER_INSTRUCTIONS, Orchestrator
from app.routes.access import AccessResponse, resolve_access
from app.routes.receipt import forget_receipt
from app.runs import RunManager
from app.trueforge import TrueForgeError

router = APIRouter(tags=["runs"], dependencies=[Depends(require_api_key)])

KEEPALIVE_SECONDS = 15
RELEASE_CACHE_SECONDS = 2.0
# A new turn in the same session keeps the agent's context and its sandbox; the original task carries the MODE.
RESUME_PROMPT = (
    "Continue the task from where you stopped. Do not redo steps that already succeeded: pick up at the step that "
    "failed or was not finished. If the sandbox was reset or is unavailable, set up only what that step needs again "
    "(clone the repo, install Node.js and dependencies, reapply your earlier changes), then carry on. "
    "Keep the same repo and the same MODE."
)
# A run that ended, however it ended, can take another turn in the same session. resume_failed is left out:
# its turn still waits on an approval that only POST /runs/{id}/approval may answer.
RESUMABLE_STATUSES = {"cancelled", "error", "done"}
MAX_MESSAGE_LENGTH = 8000


class CreateRunRequest(BaseModel):
    # fixer needs repo and mode, policy needs repo, scout needs target.
    role: str = "fixer"
    repo: str | None = None
    mode: Literal["ship", "pr_only"] | None = None
    # org:<name>, user:<name>, or comma separated repo URLs.
    target: str | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    role: str
    # Only for fixer runs.
    access: AccessResponse | None


class RunResponse(BaseModel):
    id: str
    status: str
    repo: str
    mode: str
    via_fork: bool
    pending_action: dict[str, Any] | None
    role: str
    parent_run_id: str | None
    # The PR the run opened, from create_pull_request or a "pr" fact, recorded once. via_fork above is the run's
    # access (whether it has to work through a fork); pr_via_fork is how this PR was actually opened.
    pr_url: str | None
    pr_number: int | None
    pr_via_fork: bool | None
    # A fix run that ended without opening a PR by either route.
    stopped_without_pr: bool
    # Campaign runs: "scan" (the campaign itself, id = campaign id) or "fix" (one package), else None.
    task: str | None = None
    campaign_id: str | None = None
    package: str | None = None


class ChildRun(BaseModel):
    run_id: str
    role: str
    purpose: str | None
    status: str
    created_at: str
    result: dict[str, Any] | None


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
    # Set on 202: why the approval is recorded but the agent has not resumed yet.
    reason: str | None = None


class RunControlResponse(BaseModel):
    run_id: str
    status: str


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ReleaseResponse(BaseModel):
    merged: bool
    merge_commit_sha: str | None
    status: str | None
    conclusion: str | None
    url: str | None


def build_prompt(repo: str, mode: str) -> str:
    return f"Check https://github.com/{repo} for vulnerable packages and fix them.\nMODE: {mode}"


def scout_prompt(target: Target) -> str:
    if target.kind == "repos":
        return "Scan these repos: " + ", ".join(f"https://github.com/{owner}/{name}" for owner, name in target.repos)
    return f"Scan every repo in the GitHub {target.kind} {target.name}"


def policy_prompt(repo: str) -> str:
    return f"Draft a .greenlight.yml for https://github.com/{repo}"


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def _start(orchestrator: Orchestrator, role: str, repo: str, mode: str, message: str, via_fork: bool = False) -> str:
    try:
        return await orchestrator.start_run(role, repo, mode, message, via_fork=via_fork)
    except (TrueForgeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not start the TrueForge agent: {exc}") from exc


async def start_policy_run(orchestrator: Orchestrator, repo_url: str) -> str:
    """A top level policy run drafting .greenlight.yml; only for a repo that has no policy file."""
    try:
        owner, name = parse_repo(repo_url)
    except InvalidRepoUrl as exc:
        raise _bad_request(str(exc)) from exc
    loaded = await load_policy(owner, name)
    if loaded.exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{owner}/{name} already has {loaded.path}")
    if loaded.error is not None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=loaded.error)
    repo = f"{owner}/{name}"
    return await _start(orchestrator, "policy", repo, CHILD_MODE, policy_prompt(repo))


async def _get_run(ledger: Ledger, run_id: str) -> dict[str, Any]:
    run = await ledger.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return run


@router.post("/runs", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateRunRequest, request: Request) -> CreateRunResponse:
    state = request.app.state
    orchestrator: Orchestrator = state.orchestrator
    if body.role not in CHAT_ROLES:
        raise _bad_request(f"Role {body.role} cannot be started from the chat. Use one of: {', '.join(CHAT_ROLES)}")

    if body.role == "scout":
        if not body.target:
            raise _bad_request("A scout run needs a target: org:<name>, user:<name>, or repo URLs")
        try:
            target = parse_target(body.target)
        except InvalidTarget as exc:
            raise _bad_request(str(exc)) from exc
        run_id = await _start(orchestrator, "scout", target.key, CHILD_MODE, scout_prompt(target))
        return CreateRunResponse(run_id=run_id, role="scout", access=None)

    if body.role == "policy":
        if not body.repo:
            raise _bad_request("A policy run needs a repo")
        return CreateRunResponse(run_id=await start_policy_run(orchestrator, body.repo), role="policy", access=None)

    if not body.repo or not body.mode:
        raise _bad_request("A fixer run needs a repo and a mode")
    access = await resolve_access(state.github_client, body.repo)

    if body.mode not in access.mode_options:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mode ship needs push access to {access.repo}. Use pr_only instead.",
        )

    owner, name = access.repo.split("/", 1)
    message = f"{build_prompt(access.repo, body.mode)}\n\n{FIXER_INSTRUCTIONS}"
    extras = await build_run_message_extras(owner, name)
    if extras:
        message = f"{message}\n\n{extras}"

    run_id = await _start(orchestrator, "fixer", access.repo, body.mode, message, via_fork=access.via_fork)
    run_id_var.set(run_id)
    return CreateRunResponse(run_id=run_id, role="fixer", access=access)


class RunSummary(BaseModel):
    id: str
    repo: str
    mode: str
    via_fork: bool
    status: str
    created_at: str
    role: str


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> list[RunSummary]:
    """Top level runs only; a run's helper agent runs are at /runs/{id}/children."""
    await request.app.state.ledger.flush()
    return [RunSummary(**run) for run in await request.app.state.orchestrator.list_top_level(limit)]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: str, request: Request) -> RunResponse:
    ledger: Ledger = request.app.state.ledger
    orchestrator: Orchestrator = request.app.state.orchestrator
    await ledger.flush()
    run = await _get_run(ledger, run_id)
    role, parent_run_id = await orchestrator.meta(run_id)
    pr = await orchestrator.pr(run_id)
    info = await orchestrator.run_info(run_id)
    task = info.get("task")
    return RunResponse(
        **run,
        role=role,
        parent_run_id=parent_run_id,
        pr_url=pr["url"] if pr else None,
        pr_number=pr.get("number") if pr else None,
        pr_via_fork=pr.get("via_fork") if pr else None,
        stopped_without_pr=role == "fixer" and parent_run_id is None and task != "scan" and run["status"] == "done" and pr is None,
        task=task,
        campaign_id=run_id if task == "scan" and info.get("campaign") else info.get("campaign_id"),
        package=info.get("package"),
    )


@router.get("/runs/{run_id}/children", response_model=list[ChildRun])
async def list_children(run_id: str, request: Request) -> list[ChildRun]:
    await request.app.state.ledger.flush()
    await _get_run(request.app.state.ledger, run_id)
    return [ChildRun(**child) for child in await request.app.state.orchestrator.children(run_id)]


@router.get("/runs/{run_id}/scout", response_model=FleetSummary)
async def get_scout_board(run_id: str, request: Request) -> FleetSummary:
    """The scout agent's ranked board, as fleet rows."""
    state = request.app.state
    await state.ledger.flush()
    run = await _get_run(state.ledger, run_id)
    orchestrator: Orchestrator = state.orchestrator
    role, _ = await orchestrator.meta(run_id)
    if role != "scout":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Run {run_id} is a {role} run, not a scout run")
    result = await orchestrator.result(run_id, "scout")
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The scout has no result yet (status is {run['status']})")
    if result.get("parse_error"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The scout's reply had no readable json board")
    return scout_board(run["repo"], result)


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


@router.post(
    "/runs/{run_id}/approval",
    response_model=ApprovalResponse,
    responses={202: {"model": ApprovalResponse, "description": "Recorded, but more is needed before the agent resumes"}},
)
async def decide_approval(run_id: str, body: ApprovalRequest, request: Request, response: Response) -> ApprovalResponse:
    state = request.app.state
    ledger: Ledger = state.ledger
    manager: RunManager = state.run_manager

    async with manager.approval_lock(run_id):
        await _get_run(ledger, run_id)
        # Let queued stream writes (pending action, turn.done) land before reading the run.
        await ledger.flush()
        run = await _get_run(ledger, run_id)

        # Idempotent per (run, approver): a repeat call returns that approver's standing decision.
        prior = await approvals_for_run(ledger, run_id)
        standing = approver_decision(prior, body.approver)
        if standing is not None and standing.result == "accepted":
            return ApprovalResponse(run_id=run_id, status=run["status"], replayed=True, **_decision(asdict(standing)))
        if standing is not None and run["status"] == "awaiting_approval":
            response.status_code = status.HTTP_202_ACCEPTED
            reason = standing.result.removeprefix(NEEDS_MORE_PREFIX)
            return ApprovalResponse(run_id=run_id, status=run["status"], replayed=True, reason=reason, **_decision(asdict(standing)))

        tool_name, arguments = "unknown", None
        try:
            action, tool = await load_pending(run, ledger, state.trueforge_client)
            tool_name, arguments = tool.name or "unknown", tool.arguments
            # Feature checks only gate approvals; a reject is always allowed through.
            if body.decision == "approve":
                await check_merge(run, tool, state.github_client)
                verdict = await run_approval_checks(approval_context(run, tool, body.approver, prior))
                if isinstance(verdict, Deny):
                    raise ApprovalRefused(verdict.reason)
                if isinstance(verdict, NeedMore):
                    waiting = await ledger.append_approval(
                        run_id=run_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        decision=body.decision,
                        approver=body.approver,
                        result=f"{NEEDS_MORE_PREFIX}{verdict.reason}",
                    )
                    response.status_code = status.HTTP_202_ACCEPTED
                    return ApprovalResponse(
                        run_id=run_id, status=run["status"], replayed=False, reason=verdict.reason, **_decision(waiting)
                    )
        except ApprovalRefused as exc:
            await ledger.append_approval(
                run_id=run_id,
                tool_name=tool_name,
                arguments=arguments,
                decision=body.decision,
                approver=body.approver,
                result=f"refused: {exc.reason}",
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason) from exc

        # The decision is durable before TrueForge hears about it.
        decision = await ledger.append_approval(
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


@router.post("/runs/{run_id}/pause", response_model=RunControlResponse)
async def pause_run(run_id: str, request: Request) -> RunControlResponse:
    """Stop the agent now. TrueForge cancels the running turn; the stream then ends it with turn.done."""
    state = request.app.state
    await state.ledger.flush()
    run = await _get_run(state.ledger, run_id)
    if run["status"] != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Run is not running (status is {run['status']})")
    try:
        await state.trueforge_client.cancel(run["session_id"])
    except (TrueForgeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"TrueForge did not pause the run: {exc}") from exc
    return RunControlResponse(run_id=run_id, status="pausing")


async def _pending_question(ledger: Ledger, run: dict[str, Any]) -> tuple[str, list[str]]:
    """(thread_id, tool_call_ids) of the question the current turn paused on, from the ledger."""
    base = await ledger.turn_base(run["id"], run["turn_id"])
    cursor = await ledger.db.execute(
        "SELECT payload_json FROM events WHERE run_id = ? AND type = 'tool.response_required' AND sequence > ?"
        " ORDER BY sequence DESC LIMIT 1",
        (run["id"], base),
    )
    row = await cursor.fetchone()
    event = json.loads(row["payload_json"]) if row else {}
    ids = [call["id"] for call in event.get("tool_calls") or [] if isinstance(call, dict) and call.get("id")]
    if not event.get("thread_id") or not ids:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The agent's question could not be found in the ledger")
    return event["thread_id"], ids


async def _continue(run_id: str, request: Request, text: str | None) -> RunControlResponse:
    """Take the run's next turn in the same session: the resume prompt, a message, or the answer to its question.

    Approvals never go through here: a run waiting on a merge must use POST /runs/{id}/approval.
    """
    state = request.app.state
    ledger: Ledger = state.ledger
    manager: RunManager = state.run_manager
    trueforge = state.trueforge_client

    async with manager.approval_lock(run_id):
        await ledger.flush()
        run = await _get_run(ledger, run_id)
        current = run["status"]
        if current == "running":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The agent is still working. Pause it first, or wait for it to finish")
        if current == "awaiting_approval":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The agent is waiting for a decision on the merge. Approve or reject it first")
        if current == "awaiting_input" and text is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The agent asked a question. Answer it to continue")
        if current not in RESUMABLE_STATUSES and current != "awaiting_input":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Run cannot continue (status is {current})")
        try:
            if current == "awaiting_input":
                thread_id, call_ids = await _pending_question(ledger, run)
                turn = await trueforge.answer_question(run["session_id"], run["turn_id"], thread_id, call_ids, text or "")
            else:
                turn = await trueforge.start_turn(run["session_id"], text or RESUME_PROMPT)
        except (TrueForgeError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"TrueForge did not continue the run: {exc}") from exc

        base = await ledger.last_sequence(run_id)
        await ledger.begin_turn(run_id, turn.turn_id, base)
        forget_receipt(request.app, run_id)
        manager.start(run_id, run["session_id"], turn.turn_id, after_sequence=base, base=base)
        return RunControlResponse(run_id=run_id, status="running")


@router.post("/runs/{run_id}/resume", response_model=RunControlResponse)
async def resume_run(run_id: str, request: Request) -> RunControlResponse:
    """Continue a paused, stopped or finished run from where it stopped, in the same session and sandbox."""
    return await _continue(run_id, request, None)


@router.post("/runs/{run_id}/message", response_model=RunControlResponse)
async def send_message(run_id: str, body: MessageRequest, request: Request) -> RunControlResponse:
    """Say something to the run's agent: a new turn in the same session, or the answer to the question it asked."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The message is empty")
    return await _continue(run_id, request, text)
