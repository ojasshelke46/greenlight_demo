import re
import time
from typing import Any

import httpx

from app.config import Settings

GITHUB_API_URL = "https://api.github.com"
CACHE_TTL_SECONDS = 5.0

_REPO_URL_PATTERN = re.compile(
    r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


class InvalidRepoUrl(ValueError):
    pass


def parse_repo(url: str) -> tuple[str, str]:
    match = _REPO_URL_PATTERN.match(url.strip())
    if match is None or match.group(2) in {".", ".."}:
        raise InvalidRepoUrl(f"Not a GitHub repository URL: {url!r}. Expected https://github.com/owner/repo")
    return match.group(1), match.group(2)


class GitHubClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http_client
        self._token = settings.github_bot_token
        self._login = settings.github_bot_login
        self._cache: dict[str, tuple[float, httpx.Response]] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str) -> httpx.Response:
        now = time.monotonic()
        cached = self._cache.get(path)
        if cached is not None and cached[0] > now:
            return cached[1]

        response = await self._http.get(f"{GITHUB_API_URL}{path}", headers=self._headers(), timeout=10.0)
        if response.status_code in (200, 404):
            self._cache[path] = (now + CACHE_TTL_SECONDS, response)
        return response

    async def check_access(self, owner: str, repo: str) -> dict[str, Any]:
        response = await self._get(f"/repos/{owner}/{repo}")
        # GitHub answers 404 for private repos the token cannot see.
        if response.status_code == 404:
            return {"exists": False, "private": None, "default_branch": None, "bot_can_push": False}
        response.raise_for_status()

        data = response.json()
        return {
            "exists": True,
            "private": data["private"],
            "default_branch": data["default_branch"],
            "bot_can_push": bool(data.get("permissions", {}).get("push", False)),
        }

    async def get_pr(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        response = await self._get(f"/repos/{owner}/{repo}/pulls/{number}")
        response.raise_for_status()

        data = response.json()
        head_repo = data["head"].get("repo")
        return {
            "state": data["state"],
            "base_ref": data["base"]["ref"],
            "head_ref": data["head"]["ref"],
            "head_repo_owner": head_repo["owner"]["login"] if head_repo else None,
        }
