from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.auth import require_api_key
from app.features.policy import checks, loader
from app.features.policy.loader import load_policy
from app.features.policy.model import Policy, active_freeze
from app.github import InvalidRepoUrl, parse_repo


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Merged into the app's lifespan, which runs first, so app.state.github_client exists by now.
    loader.bind(app)
    try:
        yield
    finally:
        loader.bind(None)


router = APIRouter(prefix="/features/policy", tags=["policy"], dependencies=[Depends(require_api_key)], lifespan=_lifespan)


class FreezeStatus(BaseModel):
    active: bool
    ends_at: datetime | None
    # The end as the policy wrote it, with its timezone, e.g. "Mon 09:00 Asia/Kolkata".
    until: str | None


class PolicyResponse(BaseModel):
    repo: str
    path: str
    exists: bool
    # Defaults when the file is missing; None when the file is invalid.
    policy: Policy | None
    # Set when the policy is unusable, which blocks merging.
    error: str | None
    freeze: FreezeStatus


@router.get("", response_model=PolicyResponse)
async def get_policy(repo: str = Query(...)) -> PolicyResponse:
    try:
        owner, name = parse_repo(repo)
    except InvalidRepoUrl as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    loaded = await load_policy(owner, name)
    freeze = active_freeze(loaded.policy.freeze, checks.now()) if loaded.policy is not None else None
    return PolicyResponse(
        repo=f"{owner}/{name}",
        path=loaded.path,
        exists=loaded.exists,
        policy=loaded.policy,
        error=loaded.error,
        freeze=FreezeStatus(active=freeze is not None, ends_at=freeze.ends_at if freeze else None, until=freeze.until if freeze else None),
    )


class DraftResponse(BaseModel):
    run_id: str


@router.post("/draft", response_model=DraftResponse, status_code=status.HTTP_201_CREATED)
async def draft_policy(request: Request, repo: str = Query(...)) -> DraftResponse:
    """Start the policy agent drafting a .greenlight.yml, for a repo that has no policy file yet."""
    from app.routes.runs import start_policy_run

    return DraftResponse(run_id=await start_policy_run(request.app.state.orchestrator, repo))
