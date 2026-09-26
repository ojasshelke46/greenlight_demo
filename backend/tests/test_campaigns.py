"""Campaigns: scan first, then one package (or one repo, or one policy) at a time."""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import campaigns as campaigns_module
from app.campaigns import branch_base, build_queue
from app.config import get_settings
from app.github import GitHubClient
from app.main import create_app
from app.runs import RunManager
from tests.test_integration import AUTH, AgentsFake, fact_line, until

REPO_URL = "https://github.com/acme/widgets"

SCAN_FACTS = [
    {"package": "lodash", "from": "4.17.20", "to": "4.17.21", "advisories": ["GHSA-aaaa-1111-aaaa"], "severity": "high"},
    {"package": "next", "from": "13.0.0", "to": "14.2.30", "advisories": ["GHSA-bbbb-2222-bbbb", "GHSA-cccc-3333-cccc"], "severity": "critical"},
    {"package": "minimist", "from": "1.2.5", "to": "1.2.8", "advisories": ["GHSA-dddd-4444-dddd", "GHSA-eeee-5555-eeee", "GHSA-ffff-6666-ffff"], "severity": "high"},
    {"package": "qs", "from": "6.5.2", "to": "6.5.3", "advisories": ["GHSA-gggg-7777-gggg"], "severity": "low"},
]
ORDER = ["next", "minimist", "lodash", "qs"]


def reply(text: str, status: str = "done") -> list[dict[str, Any]]:
    return [
        {"type": "turn.created", "id": "e1"},
        {"type": "model.message", "id": "e2", "content": text},
        {"type": "turn.done", "id": "e3", "state": {"status": status}},
    ]


def pr_url(package: str) -> str:
    return f"https://github.com/acme/widgets/pull/{ORDER.index(package) + 10 if package in ORDER else 99}"


def fixer(fail: set[str] | None = None, facts: list[dict[str, Any]] = SCAN_FACTS):
    def script(message: str) -> list[dict[str, Any]]:
        if message.startswith("TASK: scan"):
            lines = [fact_line("vulnerability", **f) for f in facts] + [fact_line("scan_done", count=len(facts))]
            return reply("Scanned.\n" + "\n".join(lines))
        package = message.splitlines()[0].removeprefix("TASK: fix ").strip()
        if package in (fail or set()):
            return reply(f"Tests kept failing for {package}.")
        return reply(f"Opened a PR for {package}.\n{fact_line('pr', url=pr_url(package), number=int(pr_url(package).rsplit('/', 1)[1]), via_fork=False)}")

    return script


def github_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in ("/repos/acme/widgets", "/repos/acme/gadgets"):
        return httpx.Response(200, json={"private": False, "default_branch": "main", "permissions": {"push": True}})
    return httpx.Response(404, json={"message": "Not Found"})


@asynccontextmanager
async def api(fake: AgentsFake):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(github_handler)) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            app.state.http_client = github_http
            app.state.trueforge_client = fake
            app.state.run_manager = RunManager(app.state.ledger, fake, observer=app.state.orchestrator)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


@pytest.fixture(autouse=True)
def quick_advance(monkeypatch):
    monkeypatch.setattr(campaigns_module, "AUTO_ADVANCE_SECONDS", 0.05)


async def start(client: AsyncClient, auto: bool = False, mode: str = "pr_only") -> str:
    response = await client.post("/campaigns", json={"repo": REPO_URL, "mode": mode, "auto": auto})
    assert response.status_code == 201, response.text
    return response.json()["campaign_id"]


async def campaign(client: AsyncClient, campaign_id: str) -> dict[str, Any]:
    response = await client.get(f"/campaigns/{campaign_id}")
    assert response.status_code == 200, response.text
    return response.json()


def statuses(state: dict[str, Any]) -> dict[str, str]:
    return {item["package"]: item["status"] for item in state["items"]}


async def wait_until(client: AsyncClient, campaign_id: str, check) -> dict[str, Any]:
    result: dict[str, Any] = {}

    async def ready() -> bool:
        result.update(await campaign(client, campaign_id))
        return bool(check(result))

    await until(ready, timeout=8)
    return result


# Pure pieces


def test_queue_is_ordered_by_severity_then_advisory_count():
    assert [i["package"] for i in build_queue(SCAN_FACTS)] == ORDER


def test_queue_merges_the_older_per_advisory_facts():
    items = build_queue([
        {"package": "a", "advisory": "GHSA-1", "severity": "moderate", "current": "1.0", "fixed": "1.1"},
        {"package": "a", "advisory": "GHSA-2", "severity": "critical"},
    ])
    assert items == [{"package": "a", "from": "1.0", "to": "1.1", "advisories": ["GHSA-1", "GHSA-2"], "severity": "critical"}]


def test_branch_names_are_per_package_and_advisory():
    assert branch_base("@babel/core", ["GHSA-r683-j2x4-v87g"]) == "greenlight/babel-core-r683"
    assert branch_base("lodash", []) == "greenlight/lodash-fix"


# Repo campaigns


async def test_scan_builds_the_queue_and_changes_nothing():
    fake = AgentsFake({"greenlight": fixer()})
    async with api(fake) as (client, app):
        campaign_id = await start(client)
        state = await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        runs = (await client.get("/runs")).json()

    assert [i["package"] for i in state["items"]] == ORDER
    assert set(statuses(state).values()) == {"queued"}
    assert state["next"]["package"] == "next" and state["done"] == 0 and state["total"] == 4
    assert fake.started_by("greenlight")[0].startswith("TASK: scan")
    assert [r["id"] for r in runs] == [campaign_id]


async def test_one_fix_at_a_time_then_the_next_is_a_separate_run():
    fake = AgentsFake({"greenlight": fixer()})
    gate = asyncio.Event()
    original = fake.stream_turn

    async def held(session_id, turn_id, after_sequence=None):
        if fake.turn_messages[turn_id][1].startswith("TASK: fix"):
            await gate.wait()
        async for event in original(session_id, turn_id, after_sequence):
            yield event

    fake.stream_turn = held  # type: ignore[method-assign]
    async with api(fake) as (client, app):
        campaign_id = await start(client)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        first = await client.post(f"/campaigns/{campaign_id}/next")
        busy = await client.post(f"/campaigns/{campaign_id}/next")
        other = await client.post(f"/campaigns/{campaign_id}/items/qs/fix")
        gate.set()
        await wait_until(client, campaign_id, lambda s: statuses(s)["next"] == "pr_opened")
        second = await client.post(f"/campaigns/{campaign_id}/next")
        state = await wait_until(client, campaign_id, lambda s: statuses(s)["minimist"] == "pr_opened")
        first_run = (await client.get(f"/runs/{first.json()['run_id']}")).json()

    assert first.status_code == 201 and busy.status_code == 409 and other.status_code == 409
    assert second.status_code == 201 and second.json()["run_id"] != first.json()["run_id"]
    fixes = [m for m in fake.started_by("greenlight") if m.startswith("TASK: fix")]
    assert [m.splitlines()[0] for m in fixes] == ["TASK: fix next", "TASK: fix minimist"]
    assert "BRANCH: greenlight/next-bbbb" in fixes[0] and "BRANCH: greenlight/minimist-dddd" in fixes[1]
    assert first_run["task"] == "fix" and first_run["campaign_id"] == campaign_id and first_run["package"] == "next"
    assert first_run["pr_url"] == pr_url("next")
    assert state["done"] == 2 and [i["pr_url"] for i in state["items"][:2]] == [pr_url("next"), pr_url("minimist")]


async def test_skip_moves_next_along():
    fake = AgentsFake({"greenlight": fixer()})
    async with api(fake) as (client, app):
        campaign_id = await start(client)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        skipped = await client.post(f"/campaigns/{campaign_id}/items/next/skip")
        started = await client.post(f"/campaigns/{campaign_id}/next")
        await wait_until(client, campaign_id, lambda s: statuses(s)["minimist"] == "pr_opened")
        missing = await client.post(f"/campaigns/{campaign_id}/items/nope/skip")

    assert skipped.status_code == 200 and statuses(skipped.json())["next"] == "skipped"
    assert started.status_code == 201
    assert [m.splitlines()[0] for m in fake.started_by("greenlight") if m.startswith("TASK: fix")] == ["TASK: fix minimist"]
    assert missing.status_code == 404


async def test_auto_mode_advances_after_each_pr():
    fake = AgentsFake({"greenlight": fixer()})
    async with api(fake) as (client, app):
        campaign_id = await start(client, auto=True)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        await client.post(f"/campaigns/{campaign_id}/next")
        state = await wait_until(client, campaign_id, lambda s: s["done"] == 4)

    fixes = [m.splitlines()[0] for m in fake.started_by("greenlight") if m.startswith("TASK: fix")]
    assert fixes == [f"TASK: fix {p}" for p in ORDER]
    assert all(i["status"] == "pr_opened" for i in state["items"]) and state["next"] is None


async def test_auto_mode_stops_on_the_first_failure():
    fake = AgentsFake({"greenlight": fixer(fail={"minimist"})})
    async with api(fake) as (client, app):
        campaign_id = await start(client, auto=True)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        await client.post(f"/campaigns/{campaign_id}/next")
        state = await wait_until(client, campaign_id, lambda s: s["stopped"] is not None)
        await asyncio.sleep(0.3)
        after = await campaign(client, campaign_id)

    assert statuses(after) == {"next": "pr_opened", "minimist": "failed", "lodash": "queued", "qs": "queued"}
    assert state["stopped"]["package"] == "minimist" and "without opening a PR" in state["stopped"]["reason"]
    assert after["auto"] is False
    assert len([m for m in fake.started_by("greenlight") if m.startswith("TASK: fix")]) == 2


async def test_stop_here_cancels_the_pending_next_item(monkeypatch):
    monkeypatch.setattr(campaigns_module, "AUTO_ADVANCE_SECONDS", 1.0)
    fake = AgentsFake({"greenlight": fixer()})
    async with api(fake) as (client, app):
        campaign_id = await start(client, auto=True)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        await client.post(f"/campaigns/{campaign_id}/next")
        waiting = await wait_until(client, campaign_id, lambda s: s["next_start_in"] is not None)
        stopped = await client.post(f"/campaigns/{campaign_id}/stop")
        await asyncio.sleep(1.2)
        after = await campaign(client, campaign_id)

    assert 0 < waiting["next_start_in"] <= 1.0
    assert stopped.json()["auto"] is False and stopped.json()["next_start_in"] is None
    assert after["done"] == 1 and statuses(after)["minimist"] == "queued"


async def test_a_fix_run_without_a_pr_fixed_nothing():
    fake = AgentsFake({"greenlight": fixer(fail={"next"})})
    async with api(fake) as (client, app):
        campaign_id = await start(client)
        await wait_until(client, campaign_id, lambda s: s["scan_status"] == "done")
        run_id = (await client.post(f"/campaigns/{campaign_id}/next")).json()["run_id"]
        await wait_until(client, campaign_id, lambda s: statuses(s)["next"] == "failed")

        async def receipt_ready() -> bool:
            return (await client.get(f"/runs/{run_id}/receipt")).status_code == 200

        await until(receipt_ready, timeout=8)
        receipt = (await client.get(f"/runs/{run_id}/receipt")).json()

    assert receipt["advisories_fixed"] == 0


# Fleet and policy campaigns


async def test_fleet_scans_one_repo_at_a_time_then_starts_one_repo_campaign_at_a_time(monkeypatch):
    from app.features.fleet import scan as fleet_scan

    order: list[str] = []

    async def list_repos(http, token, target, cap):
        return [fleet_scan.RepoRef("acme", "widgets", "main"), fleet_scan.RepoRef("acme", "gadgets", "main"), fleet_scan.RepoRef("acme", "docs", "main")]

    async def scan_repo(self, ref):
        order.append(f"start {ref.name}")
        await asyncio.sleep(0.01)
        order.append(f"end {ref.name}")
        risk = {"widgets": 5, "gadgets": 10, "docs": 0}[ref.name]
        return {"repo": ref.full_name, "default_branch": "main", "status": "ok", "error": None, "packages": 3,
                "counts": {"critical": 0, "high": risk // 5, "moderate": 0, "low": 0, "unknown": 0}, "top": [], "risk": risk}

    monkeypatch.setattr(fleet_scan, "list_repos", list_repos)
    monkeypatch.setattr(fleet_scan.Scan, "scan_repo", scan_repo)
    fake = AgentsFake({"greenlight": fixer()})
    async with api(fake) as (client, app):
        created = await client.post("/campaigns/fleet", json={"target": "org:acme"})
        fleet_id = created.json()["campaign_id"]
        events = (await client.get(f"/campaigns/fleet/{fleet_id}/events")).text
        state = (await client.get(f"/campaigns/fleet/{fleet_id}")).json()
        first = await client.post(f"/campaigns/fleet/{fleet_id}/next", json={})
        busy = await client.post(f"/campaigns/fleet/{fleet_id}/next", json={})
        # Finish the first repo: fix all of it, one PR at a time.
        repo_campaign = first.json()["campaign_id"]
        await wait_until(client, repo_campaign, lambda s: s["scan_status"] == "done")
        await client.post(f"/campaigns/{repo_campaign}/auto", json={"auto": True})

        async def first_repo_done() -> bool:
            return (await client.get(f"/campaigns/fleet/{fleet_id}")).json()["active"] is None

        await until(first_repo_done, timeout=8)
        second = await client.post(f"/campaigns/fleet/{fleet_id}/next", json={})
        empty = await client.post(f"/campaigns/fleet/{fleet_id}/next", json={})

    assert order == ["start widgets", "end widgets", "start gadgets", "end gadgets", "start docs", "end docs"]
    assert events.count("event: repo") == 3 and "event: queue" in events
    assert [i["repo"] for i in state["queue"]] == ["acme/gadgets", "acme/widgets"]
    assert first.status_code == 201 and busy.status_code == 409
    assert second.status_code == 201 and empty.status_code == 409
    scans = [m for m in fake.started_by("greenlight") if m.startswith("TASK: scan")]
    assert [s.splitlines()[1] for s in scans] == ["Repository: https://github.com/acme/gadgets", "Repository: https://github.com/acme/widgets"]


def policy_script(message: str) -> list[dict[str, Any]]:
    repo = message.splitlines()[1].removeprefix("Repository: https://github.com/")
    number = 1 if repo.endswith("widgets") else 2
    return reply(f"Drafted.\n```yaml\napprovers: []\n```\n{fact_line('pr', url=f'https://github.com/{repo}/pull/{number}', number=number, via_fork=False)}")


async def test_policy_drafts_one_repo_at_a_time_and_moves_on_after_each_pr():
    fake = AgentsFake({"greenlight-policy": policy_script})
    async with api(fake) as (client, app):
        created = await client.post("/campaigns/policy", json={"repos": [REPO_URL, "https://github.com/acme/gadgets"]})
        policy_id = created.json()["campaign_id"]

        async def both_opened() -> bool:
            state = (await client.get(f"/campaigns/policy/{policy_id}")).json()
            return [i["status"] for i in state["items"]] == ["pr_opened", "pr_opened"]

        await until(both_opened, timeout=8)
        state = (await client.get(f"/campaigns/policy/{policy_id}")).json()
        first_pr_note = (await app.state.ledger.get_notes(state["items"][0]["run_id"], kind="pr"))[0]
        second_run = await app.state.ledger.get_run(state["items"][1]["run_id"])

    assert [m.splitlines()[:2] for m in fake.started_by("greenlight-policy")] == [
        ["TASK: policy", f"Repository: {REPO_URL}"],
        ["TASK: policy", "Repository: https://github.com/acme/gadgets"],
    ]
    # The second repo started only after the first repo's PR was recorded.
    assert first_pr_note["created_at"] <= second_run["created_at"]
    assert [i["pr_url"] for i in state["items"]] == ["https://github.com/acme/widgets/pull/1", "https://github.com/acme/gadgets/pull/2"]
