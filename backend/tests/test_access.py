from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.github import GitHubClient, InvalidRepoUrl, parse_repo
from app.main import create_app

AUTH = {"Authorization": "Bearer test-key"}


def repo_payload(*, private: bool, push: bool, default_branch: str = "main") -> dict:
    return {
        "private": private,
        "default_branch": default_branch,
        "permissions": {"admin": False, "push": push, "pull": True},
    }


@asynccontextmanager
async def api_with_github(routes: dict[str, httpx.Response], calls: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        if calls is not None:
            calls.append(request.url.path)
        return routes.get(request.url.path, httpx.Response(404, json={"message": "Not Found"}))

    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client


async def test_collaborator_repo_offers_ship_and_pr_only():
    routes = {"/repos/acme/widgets": httpx.Response(200, json=repo_payload(private=True, push=True))}

    async with api_with_github(routes) as client:
        response = await client.get("/access", params={"repo": "https://github.com/acme/widgets.git"})

    assert response.status_code == 200
    assert response.json() == {
        "repo": "acme/widgets",
        "private": True,
        "default_branch": "main",
        "mode_options": ["ship", "pr_only"],
        "via_fork": False,
    }


async def test_public_non_collaborator_repo_is_pr_only_via_fork():
    routes = {
        "/repos/octo/public-lib": httpx.Response(
            200, json=repo_payload(private=False, push=False, default_branch="trunk")
        )
    }

    async with api_with_github(routes) as client:
        response = await client.get("/access", params={"repo": "https://github.com/octo/public-lib/"})

    assert response.status_code == 200
    assert response.json() == {
        "repo": "octo/public-lib",
        "private": False,
        "default_branch": "trunk",
        "mode_options": ["pr_only"],
        "via_fork": True,
    }


async def test_invalid_url_is_rejected_without_calling_github():
    calls: list[str] = []

    async with api_with_github({}, calls) as client:
        response = await client.get("/access", params={"repo": "https://gitlab.com/acme/widgets"})

    assert response.status_code == 400
    assert "Expected https://github.com/owner/repo" in response.json()["detail"]
    assert calls == []


async def test_repo_not_found_returns_404():
    async with api_with_github({}) as client:
        response = await client.get("/access", params={"repo": "https://github.com/acme/secret"})

    assert response.status_code == 404
    assert "acme/secret" in response.json()["detail"]


async def test_access_requires_api_key():
    async with api_with_github({}) as client:
        response = await client.get(
            "/access", params={"repo": "https://github.com/acme/widgets"}, headers={"Authorization": "Bearer wrong"}
        )

    assert response.status_code == 401


async def test_github_responses_are_cached():
    calls: list[str] = []
    routes = {"/repos/acme/widgets": httpx.Response(200, json=repo_payload(private=False, push=True))}

    async with api_with_github(routes, calls) as client:
        for _ in range(3):
            response = await client.get("/access", params={"repo": "https://github.com/acme/widgets"})
            assert response.status_code == 200

    assert calls == ["/repos/acme/widgets"]


async def test_check_access_reports_missing_repo():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        info = await GitHubClient(http, get_settings()).check_access("acme", "gone")

    assert info == {"exists": False, "private": None, "default_branch": None, "bot_can_push": False}


async def test_get_pr_extracts_refs_and_head_owner():
    pr = {
        "state": "open",
        "base": {"ref": "main"},
        "head": {"ref": "greenlight/node-fetch", "repo": {"owner": {"login": "greenlight-agent"}}},
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=pr))

    async with httpx.AsyncClient(transport=transport) as http:
        result = await GitHubClient(http, get_settings()).get_pr("acme", "widgets", 7)

    assert result == {
        "state": "open",
        "base_ref": "main",
        "head_ref": "greenlight/node-fetch",
        "head_repo_owner": "greenlight-agent",
        "merged": False,
        "merge_commit_sha": None,
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/widgets",
        "https://github.com/acme/widgets/",
        "https://github.com/acme/widgets.git",
        "https://github.com/acme/widgets.git/",
    ],
)
def test_parse_repo_accepts_supported_forms(url):
    assert parse_repo(url) == ("acme", "widgets")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "github.com/acme/widgets",
        "http://github.com/acme/widgets",
        "https://www.github.com/acme/widgets",
        "https://gitlab.com/acme/widgets",
        "https://github.com/acme",
        "https://github.com/acme/widgets/pull/1",
        "https://github.com/acme/..",
        "git@github.com:acme/widgets.git",
    ],
)
def test_parse_repo_rejects_everything_else(url):
    with pytest.raises(InvalidRepoUrl):
        parse_repo(url)
