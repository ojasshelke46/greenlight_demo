"""Loads the target repo's policy file from its default branch through the GitHub contents API."""

from time import monotonic
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import yaml
from pydantic import ValidationError

from app import github
from app.config import get_settings
from app.features.policy.model import Policy

CACHE_TTL_SECONDS = 10.0
DEFAULT_POLICY_PATH = ".greenlight.yml"

# Approval checks and message parts get no app state, so the router's lifespan binds the app here and
# each load reads app.state.github_client when it runs: the app's one shared HTTP client, and in tests
# whatever mock the test put there.
_app: Any = None
_cache: dict[tuple[str, str], tuple[float, "LoadedPolicy"]] = {}


def bind(app: Any) -> None:
    global _app
    _app = app
    _cache.clear()


def _http() -> httpx.AsyncClient:
    if _app is None:
        raise RuntimeError("The policy feature is not started")
    return _app.state.github_client._http


@dataclass(frozen=True)
class LoadedPolicy:
    path: str
    exists: bool
    # Defaults when the file is missing; None when it could not be loaded or is invalid.
    policy: Policy | None
    # Why the policy is unusable. Set means merging is blocked.
    error: str | None



def policy_path() -> str:
    return get_settings().policy_file_path or DEFAULT_POLICY_PATH


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in exc.errors())


def parse_policy(path: str, text: str) -> LoadedPolicy:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return LoadedPolicy(path, True, None, f"{path} is not valid YAML: {str(exc).splitlines()[0]}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return LoadedPolicy(path, True, None, f"{path} must be a mapping of settings")
    try:
        return LoadedPolicy(path, True, Policy.model_validate(data), None)
    except ValidationError as exc:
        return LoadedPolicy(path, True, None, f"{path} is invalid: {_validation_message(exc)}")


async def _fetch(owner: str, repo: str, path: str) -> tuple[LoadedPolicy, bool]:
    """The loaded policy, and whether it may be cached. GitHub failures are never cached."""
    headers = {
        "Authorization": f"Bearer {get_settings().github_bot_token}",
        "Accept": "application/vnd.github.raw+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # No ref: the contents API reads the repository's default branch.
    url = f"{github.GITHUB_API_URL}/repos/{owner}/{repo}/contents/{quote(path)}"
    try:
        response = await _http().get(url, headers=headers, timeout=10.0)
    except httpx.HTTPError:
        return LoadedPolicy(path, False, None, f"Could not reach GitHub to read {path}"), False

    if response.status_code == 404:
        return LoadedPolicy(path, False, Policy(), None), True
    if response.status_code != 200:
        return LoadedPolicy(path, False, None, f"Could not read {path} from GitHub ({response.status_code})"), False
    if response.headers.get("content-type", "").startswith("application/json"):
        # A directory listing comes back as JSON even when raw content is asked for.
        return LoadedPolicy(path, True, None, f"{path} is a directory, not a policy file"), True
    return parse_policy(path, response.text), True


async def load_policy(owner: str, repo: str) -> LoadedPolicy:
    key = (owner.lower(), repo.lower())
    now = monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]

    loaded, cacheable = await _fetch(owner, repo, policy_path())
    if cacheable:
        _cache[key] = (now + CACHE_TTL_SECONDS, loaded)
    return loaded
