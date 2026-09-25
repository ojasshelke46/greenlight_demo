import asyncio
import json
from contextlib import asynccontextmanager

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.github import GitHubClient
from app.main import create_app
from app.runs import RunManager
from app.trueforge import TurnEvent, TurnHandle

AUTH = {"Authorization": "Bearer test-key"}

MERGE_ARGS = {"owner": "acme", "repo": "widgets", "pullNumber": 7}
SCRIPT = [
    {"type": "turn.created", "id": "ev_1", "turn_id": "turn_1", "created_at": "t1"},
    {
        "type": "model.message",
        "id": "ev_2",
        "thread_id": "th_1",
        "created_at": "t2",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "merge_pull_request", "arguments": json.dumps(MERGE_ARGS)},
                "tool_info": {"type": "mcp", "name": "merge_pull_request", "server_id": "s", "server_name": "github"},
            }
        ],
    },
    {
        "type": "tool.approval_required",
        "id": "ev_3",
        "thread_id": "th_1",
        "created_at": "t3",
        "tool_calls": [{"id": "call_1", "source_event_id": "ev_2"}],
    },
    {"type": "turn.done", "id": "ev_4", "thread_id": "th_1", "created_at": "t4", "state": {"status": "done"}},
]
RAW = [(index + 1, json.dumps(event)) for index, event in enumerate(SCRIPT)]


class FakeTrueForge:
    def __init__(self, hold_after: int | None = None) -> None:
        self.turns: list[tuple[str, str]] = []
        self.subscribe_calls: list[int] = []
        self.hold_after = hold_after
        self.release = asyncio.Event()

    async def get_agent_id(self, name: str | None = None) -> str:
        return "agent_1"

    async def create_session(self, agent_id: str) -> str:
        assert agent_id == "agent_1"
        return "sess_1"

    async def start_turn(self, session_id: str, message: str) -> TurnHandle:
        self.turns.append((session_id, message))
        return TurnHandle(session_id=session_id, turn_id="turn_1", status="running")

    async def stream_turn(self, session_id: str, turn_id: str, after_sequence: int | None = None):
        self.subscribe_calls.append(after_sequence or 0)
        for sequence, event in enumerate(SCRIPT, start=1):
            if sequence <= (after_sequence or 0):
                continue
            if self.hold_after is not None and sequence > self.hold_after:
                await self.release.wait()
            yield TurnEvent(sequence=sequence, type=event["type"], data=event)


def repo_payload(push: bool) -> dict:
    return {"private": False, "default_branch": "main", "permissions": {"push": push, "pull": True}}


@asynccontextmanager
async def api(fake: FakeTrueForge, *, push: bool = True):
    routes = {"/repos/acme/widgets": httpx.Response(200, json=repo_payload(push))}
    transport = httpx.MockTransport(lambda request: routes.get(request.url.path, httpx.Response(404)))

    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            app.state.trueforge_client = fake
            app.state.run_manager = RunManager(app.state.ledger, fake)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


def parse_sse(text: str) -> list[dict[str, str]]:
    events, current = [], {}
    for line in text.splitlines():
        if not line:
            if current:
                events.append(current)
            current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        current[field] = value[1:] if value.startswith(" ") else value
    if current:
        events.append(current)
    return events


async def start_run(client: AsyncClient, mode: str = "ship") -> str:
    response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


async def test_ship_is_rejected_when_bot_cannot_push():
    fake = FakeTrueForge()
    async with api(fake, push=False) as (client, _):
        response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": "ship"})

    assert response.status_code == 400
    assert "pr_only" in response.json()["detail"]
    assert fake.turns == []


async def test_pr_only_run_starts_agent_with_mode_prompt():
    fake = FakeTrueForge()
    async with api(fake, push=False) as (client, _):
        response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": "pr_only"})

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"]
    assert body["access"]["via_fork"] is True
    assert body["access"]["mode_options"] == ["pr_only"]
    assert fake.turns == [
        ("sess_1", "Check https://github.com/acme/widgets for vulnerable packages and fix them.\nMODE: pr_only")
    ]


async def test_events_stream_in_order_unchanged_and_persisted():
    async with api(FakeTrueForge()) as (client, app):
        run_id = await start_run(client)
        response = await client.get(f"/runs/{run_id}/events")
        await app.state.ledger.flush()

        run = (await client.get(f"/runs/{run_id}")).json()
        ledger = (await client.get(f"/runs/{run_id}/ledger")).json()

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert [(int(e["id"]), e["data"]) for e in parse_sse(response.text)] == RAW

    assert run["status"] == "awaiting_approval"
    assert run["mode"] == "ship" and run["via_fork"] is False and run["repo"] == "acme/widgets"
    assert run["pending_action"]["turn_id"] == "turn_1"
    assert run["pending_action"]["thread_id"] == "th_1"
    assert run["pending_action"]["tool_calls"] == [
        {
            "tool_call_id": "call_1",
            "source_event_id": "ev_2",
            "server": "github",
            "tool_name": "merge_pull_request",
            "arguments": MERGE_ARGS,
        }
    ]

    assert [(e["sequence"], e["type"]) for e in ledger["events"]] == [(i + 1, e["type"]) for i, e in enumerate(SCRIPT)]
    assert ledger["events"][1]["payload"] == SCRIPT[1]


async def test_reconnect_replays_from_ledger_after_restart():
    fake = FakeTrueForge()
    async with api(fake) as (client, app):
        run_id = await start_run(client)
        await client.get(f"/runs/{run_id}/events")
        await app.state.ledger.flush()

        app.state.run_manager = RunManager(app.state.ledger, fake)
        response = await client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "2"})

    assert [(int(e["id"]), e["data"]) for e in parse_sse(response.text)] == RAW[2:]
    assert fake.subscribe_calls == [0]


async def test_reconnect_mid_run_replays_then_continues_live():
    fake = FakeTrueForge(hold_after=2)
    async with api(fake) as (client, app):
        run_id = await start_run(client)
        while len(await app.state.ledger.events_after(run_id, 0)) < 2:
            await app.state.ledger.flush()
            await asyncio.sleep(0.01)

        request = asyncio.create_task(client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "1"}))
        await asyncio.sleep(0.05)
        fake.release.set()
        response = await asyncio.wait_for(request, timeout=5)

    assert [int(e["id"]) for e in parse_sse(response.text)] == [2, 3, 4]


async def test_events_are_append_only():
    async with api(FakeTrueForge()) as (client, app):
        run_id = await start_run(client)
        await client.get(f"/runs/{run_id}/events")
        await app.state.ledger.flush()

        with pytest.raises(aiosqlite.IntegrityError, match="append only"):
            await app.state.ledger.db.execute("UPDATE events SET type = 'x' WHERE run_id = ?", (run_id,))


async def test_unknown_run_is_404():
    async with api(FakeTrueForge()) as (client, _):
        assert (await client.get("/runs/nope")).status_code == 404
        assert (await client.get("/runs/nope/ledger")).status_code == 404
        assert (await client.get("/runs/nope/events")).status_code == 404


async def test_list_runs_newest_first():
    async with api(FakeTrueForge()) as (client, app):
        first = await start_run(client)
        second = await start_run(client, mode="pr_only")
        response = await client.get("/runs")
        limited = await client.get("/runs", params={"limit": 1})

    assert response.status_code == 200
    runs = response.json()
    assert [r["id"] for r in runs] == [second, first]
    assert runs[0]["repo"] == "acme/widgets" and runs[0]["mode"] == "pr_only"
    assert set(runs[0]) == {"id", "repo", "mode", "via_fork", "status", "created_at"}
    assert len(limited.json()) == 1
