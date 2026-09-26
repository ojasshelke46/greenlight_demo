import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette import EventSourceResponse

from app.auth import require_api_key
from app.config import get_settings
from app.features.fleet.scan import DEFAULT_MAX_REPOS, InvalidTarget, Scan, TargetUnavailable, list_repos, parse_target, summarize

router = APIRouter(prefix="/features/fleet", tags=["fleet"], dependencies=[Depends(require_api_key)])

CACHE_TTL_SECONDS = 60.0
KEEPALIVE_SECONDS = 15

# target key -> (expires at, repo results in the order they finished, summary)
_results: dict[str, tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}


def _event(name: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data)}


@router.get("/scan")
async def scan(request: Request, target: str = Query(...)) -> EventSourceResponse:
    """One "repo" event per repo as it finishes, then one "summary" event. Read only: never starts a run."""
    try:
        parsed = parse_target(target)
    except InvalidTarget as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    cached = _results.get(parsed.key)
    if cached is not None and cached[0] > monotonic():
        _, repos, summary = cached

        async def replay() -> AsyncIterator[dict[str, str]]:
            for result in repos:
                yield _event("repo", result)
            yield _event("summary", summary)

        return _stream(replay())

    settings = get_settings()
    http = request.app.state.http_client
    try:
        refs = await list_repos(http, settings.github_bot_token, parsed, settings.fleet_max_repos or DEFAULT_MAX_REPOS)
    except TargetUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    async def events() -> AsyncIterator[dict[str, str]]:
        run = Scan(http, settings.github_bot_token)
        tasks = [asyncio.create_task(run.scan_repo(ref)) for ref in refs]
        finished: list[dict[str, Any]] = []
        try:
            for next_done in asyncio.as_completed(tasks):
                result = await next_done
                finished.append(result)
                yield _event("repo", result)
            summary = summarize(parsed, finished)
            _results[parsed.key] = (monotonic() + CACHE_TTL_SECONDS, finished, summary)
            yield _event("summary", summary)
        finally:
            # The client left: stop scanning rather than finish for nobody.
            for task in tasks:
                task.cancel()
            run.close()

    return _stream(events())


def _stream(events: AsyncIterator[dict[str, str]]) -> EventSourceResponse:
    return EventSourceResponse(events, ping=KEEPALIVE_SECONDS, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
