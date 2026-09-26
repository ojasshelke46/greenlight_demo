import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from app.auth import require_api_key
from app.campaigns import CampaignError, Campaigns
from app.features.fleet.scan import InvalidTarget, parse_target
from app.github import InvalidRepoUrl, parse_repo
from app.routes.access import AccessResponse, resolve_access
from app.trueforge import TrueForgeError

router = APIRouter(prefix="/campaigns", tags=["campaigns"], dependencies=[Depends(require_api_key)])

KEEPALIVE_SECONDS = 15


class CreateCampaign(BaseModel):
    repo: str
    mode: Literal["ship", "pr_only"]
    # Fix all, one PR at a time: start the next item after each PR opens (or each approval is resolved).
    auto: bool = False


class CreateFleet(BaseModel):
    # org:<name>, user:<name>, or comma separated repo URLs.
    target: str
    mode: Literal["ship", "pr_only"] = "pr_only"


class CreatePolicy(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=25)
    auto: bool = True


class AutoToggle(BaseModel):
    auto: bool


class FleetNext(BaseModel):
    mode: Literal["ship", "pr_only"] | None = None


def _campaigns(request: Request) -> Campaigns:
    return request.app.state.campaigns


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CampaignError):
        return HTTPException(status_code=exc.status_code, detail=exc.message)
    if isinstance(exc, (TrueForgeError, httpx.HTTPError)):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not start the TrueForge agent: {exc}")
    raise exc


async def start_repo_campaign(request: Request, repo_url: str, mode: str, auto: bool) -> tuple[str, AccessResponse]:
    """The access check, then the campaign's scan run. Ship mode needs push access; otherwise it falls back to PR only."""
    access = await resolve_access(request.app.state.github_client, repo_url)
    if mode not in access.mode_options:
        mode = "pr_only"
    try:
        campaign_id = await _campaigns(request).create(access.repo, mode, auto, access.via_fork)
    except Exception as exc:
        raise _http_error(exc) from exc
    return campaign_id, access


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CreateCampaign, request: Request) -> dict[str, Any]:
    """Scan the repo first (changing nothing), then fix one package at a time."""
    access = await resolve_access(request.app.state.github_client, body.repo)
    if body.mode not in access.mode_options:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mode ship needs push access to {access.repo}. Use pr_only instead.")
    try:
        campaign_id = await _campaigns(request).create(access.repo, body.mode, body.auto, access.via_fork)
    except Exception as exc:
        raise _http_error(exc) from exc
    return {"campaign_id": campaign_id, "access": access.model_dump()}


@router.get("")
async def list_campaigns(request: Request) -> list[dict[str, Any]]:
    """Fleet and policy campaigns. Repo campaigns are their scan runs, listed with the runs."""
    return await _campaigns(request).list_all()


# Fleet: scout repos one at a time, then fix them one repo at a time.


@router.post("/fleet", status_code=status.HTTP_201_CREATED)
async def create_fleet(body: CreateFleet, request: Request) -> dict[str, Any]:
    try:
        parse_target(body.target)
    except InvalidTarget as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"campaign_id": await _campaigns(request).create_fleet(body.target, body.mode)}


@router.get("/fleet/{campaign_id}")
async def get_fleet(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        return await _campaigns(request).get_fleet(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


@router.get("/fleet/{campaign_id}/events")
async def fleet_events(campaign_id: str, request: Request) -> EventSourceResponse:
    """One "repo" event per repo as its scan finishes, then one "queue" event once the fleet is ranked."""
    campaigns = _campaigns(request)
    try:
        await campaigns.get_fleet(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc

    async def events() -> AsyncIterator[dict[str, str]]:
        sent = 0
        updates = await campaigns.fleet_updates(campaign_id)
        while True:
            updates.clear()
            state = await campaigns.get_fleet(campaign_id)
            for result in state["repos"][sent:]:
                yield {"event": "repo", "data": json.dumps(result)}
            sent = len(state["repos"])
            if state["status"] != "scanning":
                yield {"event": "queue", "data": json.dumps(state)}
                return
            try:
                await asyncio.wait_for(updates.wait(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                pass

    return EventSourceResponse(events(), ping=KEEPALIVE_SECONDS, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/fleet/{campaign_id}/next", status_code=status.HTTP_201_CREATED)
async def fleet_next(campaign_id: str, body: FleetNext, request: Request) -> dict[str, Any]:
    campaigns = _campaigns(request)

    async def start(repo: str, mode: str) -> str:
        repo_campaign_id, _ = await start_repo_campaign(request, f"https://github.com/{repo}", body.mode or mode, False)
        return repo_campaign_id

    try:
        return {"campaign_id": await campaigns.fleet_next(campaign_id, start)}
    except (CampaignError, TrueForgeError, httpx.HTTPError) as exc:
        raise _http_error(exc) from exc


# Policy: one .greenlight.yml PR per repo, one repo at a time.


@router.post("/policy", status_code=status.HTTP_201_CREATED)
async def create_policy(body: CreatePolicy, request: Request) -> dict[str, Any]:
    repos = []
    for value in body.repos:
        try:
            owner, name = parse_repo(value)
        except InvalidRepoUrl as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if f"{owner}/{name}" not in repos:
            repos.append(f"{owner}/{name}")
    try:
        return {"campaign_id": await _campaigns(request).create_policy(repos, body.auto)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/policy/{campaign_id}")
async def get_policy(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        return await _campaigns(request).get_policy(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


@router.post("/policy/{campaign_id}/next")
async def policy_next(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"run_id": await _campaigns(request).policy_next(campaign_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/policy/{campaign_id}/stop")
async def policy_stop(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        await _campaigns(request).policy_stop(campaign_id)
        return await _campaigns(request).get_policy(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


# Repo campaigns (declared last: /{campaign_id} would otherwise match /fleet and /policy).


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        return await _campaigns(request).get(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


@router.post("/{campaign_id}/next", status_code=status.HTTP_201_CREATED)
async def campaign_next(campaign_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"run_id": await _campaigns(request).start_fix(campaign_id, None)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{campaign_id}/items/{package:path}/fix", status_code=status.HTTP_201_CREATED)
async def fix_item(campaign_id: str, package: str, request: Request) -> dict[str, Any]:
    try:
        return {"run_id": await _campaigns(request).start_fix(campaign_id, package)}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{campaign_id}/items/{package:path}/skip")
async def skip_item(campaign_id: str, package: str, request: Request) -> dict[str, Any]:
    try:
        await _campaigns(request).skip(campaign_id, package)
        return await _campaigns(request).get(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


@router.post("/{campaign_id}/auto")
async def set_auto(campaign_id: str, body: AutoToggle, request: Request) -> dict[str, Any]:
    campaigns = _campaigns(request)
    try:
        await campaigns.set_auto(campaign_id, body.auto)
        state = await campaigns.get(campaign_id)
        # Turning auto on with nothing running starts the next item right away (after the usual pause).
        if body.auto and state["active"] is None and state["next"] is not None and state["scan_status"] == "done":
            campaigns.schedule_next(campaign_id)
        return await campaigns.get(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc


@router.post("/{campaign_id}/stop")
async def stop(campaign_id: str, request: Request) -> dict[str, Any]:
    """Stop here: nothing new starts automatically. A fix already running carries on to its PR."""
    try:
        await _campaigns(request).stop(campaign_id)
        return await _campaigns(request).get(campaign_id)
    except CampaignError as exc:
        raise _http_error(exc) from exc
