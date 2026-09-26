"""Read only fleet scan: list a target's repos, read each package-lock.json, and look every installed
package version up in OSV. Never starts an agent run or a sandbox."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app import github
from app.features.fleet.lockfile import LockfileError, Pair, parse_lockfile
from app.github import InvalidRepoUrl, parse_repo

logger = logging.getLogger(__name__)

OSV_URL = "https://api.osv.dev/v1"
OSV_BATCH = 1000
REPO_CONCURRENCY = 5
DETAIL_CONCURRENCY = 10
TIMEOUT = 20.0
DEFAULT_MAX_REPOS = 25

SEVERITIES = ("critical", "high", "moderate", "low", "unknown")
WEIGHT = {"critical": 10, "high": 5, "moderate": 2, "low": 1, "unknown": 0}
_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_OWNER_TARGET = re.compile(rf"^(org|user):({_LOGIN})$", re.IGNORECASE)


class InvalidTarget(ValueError):
    pass


class TargetUnavailable(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Target:
    kind: Literal["org", "user", "repos"]
    name: str | None
    repos: tuple[tuple[str, str], ...]

    @property
    def key(self) -> str:
        if self.kind == "repos":
            return "repos:" + ",".join(f"{o}/{r}".lower() for o, r in sorted(self.repos))
        return f"{self.kind}:{self.name.lower()}"


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str
    default_branch: str | None
    error: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_target(value: str) -> Target:
    value = value.strip()
    match = _OWNER_TARGET.match(value)
    if match:
        return Target(kind=match.group(1).lower(), name=match.group(2), repos=())
    repos: list[tuple[str, str]] = []
    for part in (p.strip() for p in value.split(",")):
        if not part:
            continue
        try:
            repo = parse_repo(part)
        except InvalidRepoUrl as exc:
            raise InvalidTarget(f"{exc}. A target is org:<name>, user:<name>, or comma separated repo URLs") from exc
        if repo not in repos:
            repos.append(repo)
    if not repos:
        raise InvalidTarget("A target is org:<name>, user:<name>, or comma separated repo URLs")
    return Target(kind="repos", name=None, repos=tuple(repos))


def _headers(token: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}


async def list_repos(http: httpx.AsyncClient, token: str, target: Target, cap: int) -> list[RepoRef]:
    """The repos to scan: at most cap, archived repos and forks left out of org and user listings."""
    if target.kind == "repos":
        return list(await asyncio.gather(*(_repo_ref(http, token, owner, name) for owner, name in target.repos[:cap])))

    path = f"/orgs/{target.name}/repos" if target.kind == "org" else f"/users/{target.name}/repos"
    params = {"per_page": "100", "sort": "pushed", "type": "all" if target.kind == "org" else "owner"}
    refs: list[RepoRef] = []
    page = 1
    while len(refs) < cap:
        try:
            response = await http.get(f"{github.GITHUB_API_URL}{path}", params={**params, "page": str(page)}, headers=_headers(token), timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise TargetUnavailable(f"Could not reach GitHub to list {target.kind} {target.name}", 502) from exc
        if response.status_code == 404:
            raise TargetUnavailable(f"GitHub {target.kind} {target.name} was not found or is not visible to the bot", 404)
        if response.status_code != 200:
            raise TargetUnavailable(f"GitHub returned {response.status_code} listing {target.kind} {target.name}", 502)
        listed = response.json()
        for repo in listed:
            if repo.get("archived") or repo.get("fork"):
                continue
            refs.append(RepoRef(owner=repo["owner"]["login"], name=repo["name"], default_branch=repo.get("default_branch")))
            if len(refs) == cap:
                break
        if len(listed) < 100:
            break
        page += 1
    return refs


async def _repo_ref(http: httpx.AsyncClient, token: str, owner: str, name: str) -> RepoRef:
    try:
        response = await http.get(f"{github.GITHUB_API_URL}/repos/{owner}/{name}", headers=_headers(token), timeout=TIMEOUT)
    except httpx.HTTPError:
        return RepoRef(owner, name, None, "Could not reach GitHub")
    if response.status_code == 404:
        return RepoRef(owner, name, None, "Repository not found or not visible to the bot")
    if response.status_code != 200:
        return RepoRef(owner, name, None, f"GitHub returned {response.status_code} for the repository")
    return RepoRef(owner, name, response.json().get("default_branch"))


def _severity(detail: dict[str, Any]) -> str:
    value = str((detail.get("database_specific") or {}).get("severity") or "").lower()
    value = "moderate" if value == "medium" else value
    return value if value in WEIGHT else "unknown"


class Scan:
    """State shared by every repo in one scan, so each package version and each advisory is asked for once."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._token = token
        self._queries: dict[Pair, asyncio.Future[list[str]]] = {}
        self._details: dict[str, asyncio.Task[dict[str, str]]] = {}
        self._detail_slots = asyncio.Semaphore(DETAIL_CONCURRENCY)
        self.repo_slots = asyncio.Semaphore(REPO_CONCURRENCY)

    def close(self) -> None:
        for task in self._details.values():
            task.cancel()

    async def vuln_ids(self, pairs: set[Pair]) -> dict[Pair, list[str]]:
        loop = asyncio.get_running_loop()
        mine = sorted(p for p in pairs if p not in self._queries)
        for pair in mine:
            self._queries[pair] = loop.create_future()
        try:
            for start in range(0, len(mine), OSV_BATCH):
                await self._query_batch(mine[start : start + OSV_BATCH])
        except Exception as exc:
            for pair in mine:
                future = self._queries[pair]
                if not future.done():
                    future.set_exception(exc)
                    future.exception()  # other repos awaiting it still see the error; mark it retrieved here
            raise
        # Pairs another repo is already asking about are awaited, not asked again.
        return {pair: await self._queries[pair] for pair in pairs}

    async def _query_batch(self, pairs: list[Pair]) -> None:
        body = {"queries": [{"package": {"name": name, "ecosystem": "npm"}, "version": version} for name, version in pairs]}
        response = await self._http.post(f"{OSV_URL}/querybatch", json=body, timeout=TIMEOUT)
        response.raise_for_status()
        results = response.json().get("results") or []
        if len(results) != len(pairs):
            raise ValueError(f"OSV answered {len(results)} of {len(pairs)} queries")
        for pair, result in zip(pairs, results):
            self._queries[pair].set_result([v["id"] for v in (result or {}).get("vulns") or []])

    async def advisories(self, ids: set[str]) -> dict[str, dict[str, str]]:
        for vuln_id in ids:
            if vuln_id not in self._details:
                self._details[vuln_id] = asyncio.create_task(self._fetch_detail(vuln_id))
        return {vuln_id: await self._details[vuln_id] for vuln_id in ids}

    async def _fetch_detail(self, vuln_id: str) -> dict[str, str]:
        async with self._detail_slots:
            try:
                response = await self._http.get(f"{OSV_URL}/vulns/{vuln_id}", timeout=TIMEOUT)
                response.raise_for_status()
                detail = response.json()
            except (httpx.HTTPError, ValueError):
                logger.warning("Could not read OSV advisory %s; its severity is unknown", vuln_id)
                return {"severity": "unknown", "summary": ""}
        return {"severity": _severity(detail), "summary": str(detail.get("summary") or "")}

    async def scan_repo(self, ref: RepoRef) -> dict[str, Any]:
        async with self.repo_slots:
            try:
                return await self._scan_repo(ref)
            except Exception:
                logger.exception("Scanning %s failed", ref.full_name)
                return _result(ref, "error", error="Unexpected error while scanning this repository")

    async def _scan_repo(self, ref: RepoRef) -> dict[str, Any]:
        if ref.error:
            return _result(ref, "error", error=ref.error)

        params = {"ref": ref.default_branch} if ref.default_branch else None
        try:
            response = await self._http.get(
                f"{github.GITHUB_API_URL}/repos/{ref.owner}/{ref.name}/contents/package-lock.json",
                params=params,
                headers=_headers(self._token, accept="application/vnd.github.raw+json"),
                timeout=TIMEOUT,
            )
        except httpx.HTTPError:
            return _result(ref, "error", error="Could not reach GitHub to read package-lock.json")
        if response.status_code == 404:
            return _result(ref, "no_lockfile")
        if response.status_code != 200:
            return _result(ref, "error", error=f"GitHub returned {response.status_code} for package-lock.json")

        try:
            pairs = parse_lockfile(response.text)
        except LockfileError as exc:
            return _result(ref, "error", error=str(exc))

        try:
            found = await self.vuln_ids(pairs)
        except (httpx.HTTPError, ValueError, KeyError):
            return _result(ref, "error", error="Could not query OSV for this repository's packages", packages=len(pairs))

        # One entry per advisory: the first package version it was found on.
        hits: dict[str, Pair] = {}
        for pair in sorted(found):
            for vuln_id in found[pair]:
                hits.setdefault(vuln_id, pair)
        details = await self.advisories(set(hits))

        counts = dict.fromkeys(SEVERITIES, 0)
        advisories = []
        for vuln_id, (package, version) in hits.items():
            severity = details[vuln_id]["severity"]
            counts[severity] += 1
            advisories.append({"id": vuln_id, "package": package, "version": version, "severity": severity, "summary": details[vuln_id]["summary"]})
        advisories.sort(key=lambda a: (-WEIGHT[a["severity"]], SEVERITIES.index(a["severity"]), a["id"]))
        return _result(ref, "ok", packages=len(pairs), counts=counts, top=advisories[:3])


def _result(ref: RepoRef, status: str, *, error: str | None = None, packages: int = 0, counts: dict[str, int] | None = None, top: list[dict[str, str]] | None = None) -> dict[str, Any]:
    counts = counts or dict.fromkeys(SEVERITIES, 0)
    return {
        "repo": ref.full_name,
        "default_branch": ref.default_branch,
        "status": status,
        "error": error,
        "packages": packages,
        "counts": counts,
        "top": top or [],
        "risk": sum(WEIGHT[severity] * count for severity, count in counts.items()),
    }


def summarize(target: Target, results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(SEVERITIES, 0)
    for result in results:
        for severity, count in result["counts"].items():
            counts[severity] += count
    return {
        "target": target.key,
        "totals": {
            "repos": len(results),
            "ok": sum(r["status"] == "ok" for r in results),
            "no_lockfile": sum(r["status"] == "no_lockfile" for r in results),
            "error": sum(r["status"] == "error" for r in results),
            "counts": counts,
            "risk": sum(r["risk"] for r in results),
        },
        "repos": sorted(results, key=lambda r: (-r["risk"], r["repo"].lower())),
    }
