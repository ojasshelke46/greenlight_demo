from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.auth import require_api_key
from app.github import GitHubClient, InvalidRepoUrl, parse_repo

router = APIRouter(tags=["access"], dependencies=[Depends(require_api_key)])


class AccessResponse(BaseModel):
    repo: str
    private: bool
    default_branch: str
    mode_options: list[Literal["ship", "pr_only"]]
    via_fork: bool


async def resolve_access(github: GitHubClient, repo: str) -> AccessResponse:
    try:
        owner, name = parse_repo(repo)
    except InvalidRepoUrl as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        info = await github.check_access(owner, name)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not check the repository on GitHub") from exc

    if not info["exists"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository {owner}/{name} was not found or is not visible to the bot",
        )

    can_push = info["bot_can_push"]
    return AccessResponse(
        repo=f"{owner}/{name}",
        private=info["private"],
        default_branch=info["default_branch"],
        mode_options=["ship", "pr_only"] if can_push else ["pr_only"],
        via_fork=not can_push,
    )


@router.get("/access", response_model=AccessResponse)
async def access(request: Request, repo: str = Query(...)) -> AccessResponse:
    return await resolve_access(request.app.state.github_client, repo)
