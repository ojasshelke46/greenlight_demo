import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.github import GitHubClient
from app.main import create_app
from app.runs import RunManager
from app.trueforge import PendingAction, TurnEvent, TurnHandle

AUTH = {"Authorization": "Bearer test-key"}
REPO_URL = "https://github.com/acme/widgets"
MERGE_ARGS = {"owner": "acme", "repo": "widgets", "pullNumber": 7}
MERGE_SHA = "abc123"


def call_tool(tool_name: str = "merge_pull_request", args: Any = MERGE_ARGS, call_id: str = "call_1") -> dict:
    """A tool call the way the agent makes it: MCP tools go through the call_tool system tool."""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "call_tool",
            "arguments": json.dumps({"mcp_server": "github", "tool_name": tool_name, "input": args}),
        },
        "tool_info": {"type": "truefoundry-system", "name": "call_tool"},
    }


def pause_script(calls: list[dict]) -> list[dict]:
    """Live stream shape: an empty model.message, then deltas carrying tool call fragments."""
    events = [
        {"type": "turn.created", "id": "ev_1", "turn_id": "turn_1", "created_at": "t"},
        {"type": "model.message", "id": "ev_2", "thread_id": "th_1", "created_at": "t", "content": None},
    ]
    for index, call in enumerate(calls):
        arguments = call["function"]["arguments"]
        head = {
            "index": index,
            "id": call["id"],
            "type": "function",
            "function": {"name": call["function"]["name"], "arguments": arguments[:10]},
            "tool_info": call["tool_info"],
        }
        tail = {"index": index, "function": {"arguments": arguments[10:]}}
        for fragment in (head, tail):
            events.append({"type": "model.message.delta", "id": "ev_2", "thread_id": "th_1", "tool_calls": [fragment]})
    refs = [{"id": call["id"], "source_event_id": "ev_2"} for call in calls]
    events.append({"type": "tool.approval_required", "id": "ev_3", "thread_id": "th_1", "created_at": "t", "tool_calls": refs})
    events.append({"type": "turn.done", "id": "ev_4", "thread_id": None, "created_at": "t", "state": {"status": "done"}})
    return events


RESUMED = [
    {"type": "turn.created", "id": "ev_5", "turn_id": "turn_2", "created_at": "t"},
    {"type": "tool.response", "id": "ev_6", "thread_id": "th_1", "tool_call_id": "call_1", "content": "merged"},
    {"type": "turn.done", "id": "ev_7", "thread_id": None, "created_at": "t", "state": {"status": "done"}},
]


class FakeTrueForge:
    def __init__(self, calls: list[dict] | None = None, hold_before: str | None = None) -> None:
        self.calls = calls or [call_tool()]
        self.scripts = {"turn_1": pause_script(self.calls), "turn_2": RESUMED}
        self.stored_calls = {call["id"]: call for call in self.calls}
        self.hold_before = hold_before
        self.release = asyncio.Event()
        self.resumes: list[tuple[str, PendingAction, bool, str | None]] = []
        self.decision_recorded_before_resume: list[bool] = []
        self.ledger = None

    async def get_agent_id(self, name: str | None = None) -> str:
        return "agent_1"

    async def create_session(self, agent_id: str) -> str:
        return "sess_1"

    async def start_turn(self, session_id: str, message: str) -> TurnHandle:
        return TurnHandle(session_id=session_id, turn_id="turn_1", status="running")

    async def stream_turn(self, session_id: str, turn_id: str, after_sequence: int | None = None):
        for sequence, event in enumerate(self.scripts[turn_id], start=1):
            if sequence <= (after_sequence or 0):
                continue
            if turn_id == "turn_1" and event["type"] == self.hold_before:
                await self.release.wait()
            yield TurnEvent(sequence=sequence, type=event["type"], data=event)

    async def get_tool_call(self, session_id: str, turn_id: str, tool_call_id: str, source_event_id: str):
        assert source_event_id == "ev_2"
        return self.stored_calls.get(tool_call_id)

    async def resume_with_approval(
        self, session_id: str, pending_action: PendingAction, approved: bool, reason: str | None = None
    ) -> TurnHandle:
        cursor = await self.ledger.db.execute("SELECT COUNT(*) FROM approvals WHERE result = 'accepted'")
        self.decision_recorded_before_resume.append((await cursor.fetchone())[0] == 1)
        self.resumes.append((session_id, pending_action, approved, reason))
        await asyncio.sleep(0.01)  # widen the window for concurrent approval calls
        return TurnHandle(session_id=session_id, turn_id="turn_2", status="running")


def github_state(**overrides: Any) -> dict[str, Any]:
    state = {
        "push": True,
        "default_branch": "main",
        "pr_status": 200,
        "pr": {
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "base": {"ref": "main"},
            "head": {"ref": "greenlight/node-fetch", "repo": {"owner": {"login": "acme"}}},
        },
        "workflow_runs": [],
        "calls": [],
    }
    state.update(overrides)
    return state


@asynccontextmanager
async def api(fake: FakeTrueForge, gh: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        gh["calls"].append(path)
        if path == "/repos/acme/widgets":
            return httpx.Response(
                200,
                json={"private": False, "default_branch": gh["default_branch"], "permissions": {"push": gh["push"]}},
            )
        if path == "/repos/acme/widgets/pulls/7":
            return httpx.Response(gh["pr_status"], json=gh["pr"])
        if path == "/repos/acme/widgets/actions/runs":
            return httpx.Response(200, json={"workflow_runs": gh["workflow_runs"]})
        return httpx.Response(404, json={"message": "Not Found"})

    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            app.state.trueforge_client = fake
            app.state.run_manager = RunManager(app.state.ledger, fake)
            fake.ledger = app.state.ledger
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


async def start_run(client: AsyncClient, mode: str = "ship") -> str:
    response = await client.post("/runs", json={"repo": REPO_URL, "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


async def wait_for_status(app, run_id: str, status: str) -> None:
    async def poll() -> None:
        while True:
            await app.state.ledger.flush()
            run = await app.state.ledger.get_run(run_id)
            if run["status"] == status and await app.state.ledger.has_event(run_id, "turn.done"):
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5)


async def paused_run(client: AsyncClient, app, mode: str = "ship") -> str:
    run_id = await start_run(client, mode)
    await wait_for_status(app, run_id, "awaiting_approval")
    return run_id


def approve(client: AsyncClient, run_id: str, decision: str = "approve"):
    return client.post(f"/runs/{run_id}/approval", json={"decision": decision, "approver": "ojas"})


async def approvals(client: AsyncClient, run_id: str) -> list[dict]:
    return (await client.get(f"/runs/{run_id}/ledger")).json()["approvals"]


def sse_ids_and_types(text: str) -> list[tuple[int, str]]:
    out, current = [], {}
    for line in text.splitlines() + [""]:
        if not line:
            if "id" in current:
                out.append((int(current["id"]), json.loads(current["data"])["type"]))
            current = {}
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            current[field] = value.removeprefix(" ")
    return out


async def test_approve_records_decision_then_resumes_through_same_event_stream():
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        run = (await client.get(f"/runs/{run_id}")).json()
        assert run["pending_action"]["tool_calls"][0]["tool_name"] == "merge_pull_request"
        assert run["pending_action"]["tool_calls"][0]["arguments"] == MERGE_ARGS

        response = await approve(client, run_id)
        assert response.status_code == 200, response.text
        assert response.json()["decision"] == "approve"
        assert response.json()["replayed"] is False

        await wait_for_status(app, run_id, "done")
        events = await client.get(f"/runs/{run_id}/events")
        trail = await approvals(client, run_id)

    assert fake.decision_recorded_before_resume == [True]
    [(session_id, action, approved, reason)] = fake.resumes
    assert (session_id, approved, reason) == ("sess_1", True, None)
    assert action == PendingAction(turn_id="turn_1", thread_id="th_1", tool_call_ids=["call_1"])

    first_turn = [(i + 1, e["type"]) for i, e in enumerate(pause_script(fake.calls))]
    base = len(first_turn)
    resumed = [(base + i + 1, e["type"]) for i, e in enumerate(RESUMED)]
    assert sse_ids_and_types(events.text) == first_turn + resumed

    assert [(a["decision"], a["approver"], a["result"]) for a in trail] == [("approve", "ojas", "accepted")]
    assert trail[0]["tool_name"] == "merge_pull_request" and trail[0]["arguments"] == MERGE_ARGS


async def test_reject_resumes_with_deny_even_for_pr_only_run():
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app, mode="pr_only")
        response = await approve(client, run_id, "reject")

    assert response.status_code == 200, response.text
    [(_, _, approved, reason)] = fake.resumes
    assert approved is False
    assert reason == "Rejected by ojas"


async def test_second_approval_returns_first_result_and_never_resumes_twice():
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        first = await approve(client, run_id)
        second = await approve(client, run_id, "reject")
        trail = await approvals(client, run_id)

    assert first.status_code == second.status_code == 200
    assert second.json()["replayed"] is True
    for key in ("decision", "approver", "decided_at"):
        assert second.json()[key] == first.json()[key]
    assert len(fake.resumes) == 1
    assert len(trail) == 1


async def test_concurrent_approvals_resume_once():
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        responses = await asyncio.gather(*(approve(client, run_id) for _ in range(5)))

    assert [r.status_code for r in responses] == [200] * 5
    assert sorted(r.json()["replayed"] for r in responses) == [False, True, True, True, True]
    assert len(fake.resumes) == 1


async def assert_refused(fake: FakeTrueForge, gh: dict, reason: str, *, mode: str = "ship", paused: bool = True) -> None:
    async with api(fake, gh) as (client, app):
        run_id = await (paused_run(client, app, mode) if paused else start_run(client, mode))
        response = await approve(client, run_id)
        trail = await approvals(client, run_id)
        run = (await client.get(f"/runs/{run_id}")).json()
        fake.release.set()

    assert response.status_code == 409, response.text
    assert reason in response.json()["detail"]
    assert fake.resumes == []
    assert [a["result"] for a in trail] == [f"refused: {response.json()['detail']}"]
    assert run["status"] != "running" or not paused


async def test_refuses_when_run_has_no_pending_action():
    fake = FakeTrueForge(hold_before="tool.approval_required")
    await assert_refused(fake, github_state(), "no pending action", paused=False)


async def test_refuses_while_agent_has_not_finished_pausing():
    fake = FakeTrueForge(hold_before="turn.done")
    async with api(fake, github_state()) as (client, app):
        run_id = await start_run(client)

        async def pending_recorded() -> None:
            while (await app.state.ledger.get_run(run_id))["pending_action"] is None:
                await app.state.ledger.flush()
                await asyncio.sleep(0.01)

        await asyncio.wait_for(pending_recorded(), timeout=5)
        response = await approve(client, run_id)
        fake.release.set()

    assert response.status_code == 409
    assert "not finished pausing" in response.json()["detail"]
    assert fake.resumes == []


async def test_refuses_more_than_one_pending_action():
    fake = FakeTrueForge(calls=[call_tool(), call_tool("create_branch", call_id="call_2")])
    await assert_refused(fake, github_state(), "2 pending actions")


async def test_refuses_any_other_gated_tool():
    fake = FakeTrueForge(calls=[call_tool("delete_file", {"owner": "acme", "repo": "widgets", "path": "x"})])
    await assert_refused(fake, github_state(), "only github/merge_pull_request")


async def test_refuses_when_stored_tool_call_differs_from_stream():
    fake = FakeTrueForge()
    fake.stored_calls["call_1"] = call_tool("delete_file", MERGE_ARGS)
    await assert_refused(fake, github_state(), "github/delete_file")


async def test_refuses_merge_on_pr_only_run():
    await assert_refused(FakeTrueForge(), github_state(), "only ship runs may merge", mode="pr_only")


async def test_refuses_merge_on_fork_run():
    fake = FakeTrueForge()
    async with api(fake, github_state()) as (client, app):
        # Access checks never offer ship with a fork, so build the run directly to prove the gate holds anyway.
        await app.state.ledger.create_run(
            run_id="fork_run", repo="acme/widgets", mode="ship", via_fork=True, session_id="sess_1", turn_id="turn_1"
        )
        app.state.run_manager.start("fork_run", "sess_1", "turn_1")
        await wait_for_status(app, "fork_run", "awaiting_approval")
        response = await approve(client, "fork_run")

    assert response.status_code == 409
    assert "fork runs never merge" in response.json()["detail"]
    assert fake.resumes == []


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ({"owner": "evil", "repo": "widgets", "pullNumber": 7}, "not the run's repo"),
        ({"owner": "acme", "repo": "other", "pullNumber": 7}, "not the run's repo"),
        ({"owner": "acme", "repo": "widgets", "pullNumber": "7"}, "no valid pullNumber"),
        ({"owner": "acme", "repo": "widgets"}, "no valid pullNumber"),
    ],
)
async def test_refuses_merge_arguments_outside_the_run(args, reason):
    await assert_refused(FakeTrueForge(calls=[call_tool(args=args)]), github_state(), reason)


def pr(**changes: Any) -> dict[str, Any]:
    base = github_state()["pr"]
    return {**base, **changes}


@pytest.mark.parametrize(
    ("gh", "reason"),
    [
        (github_state(pr_status=404), "Could not read PR #7 from GitHub (404)"),
        (github_state(pr=pr(state="closed")), "is closed, not open"),
        (github_state(pr=pr(base={"ref": "develop"})), "not the default branch main"),
        (github_state(pr=pr(head={"ref": "feature/x", "repo": {"owner": {"login": "acme"}}})), "does not start with greenlight/"),
        (
            github_state(pr=pr(head={"ref": "greenlight/node-fetch", "repo": {"owner": {"login": "greenlight-agent"}}})),
            "not the repo owner acme",
        ),
        (github_state(pr=pr(head={"ref": "greenlight/node-fetch", "repo": None})), "not the repo owner acme"),
    ],
)
async def test_refuses_pr_that_fails_github_checks(gh, reason):
    await assert_refused(FakeTrueForge(), gh, reason)


async def test_refuses_when_bot_lost_push_access_after_run_started():
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        gh["push"] = False  # the access check at run creation is cached; approval must re-check live
        response = await approve(client, run_id)

    assert response.status_code == 409
    assert "no longer has push access" in response.json()["detail"]
    assert fake.resumes == []


async def test_refused_attempt_does_not_block_a_later_valid_approval():
    fake, gh = FakeTrueForge(), github_state(push=True)
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        gh["pr"] = pr(state="closed")
        refused = await approve(client, run_id)
        gh["pr"] = pr()
        accepted = await approve(client, run_id)

    assert refused.status_code == 409
    assert accepted.status_code == 200 and accepted.json()["replayed"] is False
    assert len(fake.resumes) == 1


async def test_approval_for_unknown_run_is_404():
    async with api(FakeTrueForge(), github_state()) as (client, _):
        assert (await approve(client, "nope")).status_code == 404


async def test_release_requires_an_approved_merge():
    fake = FakeTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        before = await client.get(f"/runs/{run_id}/release")
        await approve(client, run_id, "reject")
        after_reject = await client.get(f"/runs/{run_id}/release")

    assert before.status_code == after_reject.status_code == 409


async def test_release_reports_merge_and_actions_run_with_short_cache(monkeypatch):
    fake, gh = FakeTrueForge(), github_state()
    async with api(fake, gh) as (client, app):
        run_id = await paused_run(client, app)
        await approve(client, run_id)

        not_merged = (await client.get(f"/runs/{run_id}/release")).json()

        gh["pr"] = pr(state="closed", merged=True, merge_commit_sha=MERGE_SHA)
        gh["workflow_runs"] = [
            {"status": "in_progress", "conclusion": None, "html_url": "https://github.com/acme/widgets/actions/runs/1"}
        ]
        app.state.run_manager.release_cache.clear()
        running = (await client.get(f"/runs/{run_id}/release")).json()

        gh["workflow_runs"][0].update(status="completed", conclusion="success")
        calls_before = len(gh["calls"])
        cached = (await client.get(f"/runs/{run_id}/release")).json()
        calls_while_cached = len(gh["calls"]) - calls_before

        app.state.run_manager.release_cache[run_id] = (0.0, cached)  # expire it
        finished = (await client.get(f"/runs/{run_id}/release")).json()

    assert not_merged == {"merged": False, "merge_commit_sha": None, "status": None, "conclusion": None, "url": None}
    assert running == {
        "merged": True,
        "merge_commit_sha": MERGE_SHA,
        "status": "in_progress",
        "conclusion": None,
        "url": "https://github.com/acme/widgets/actions/runs/1",
    }
    assert cached == running and calls_while_cached == 0
    assert finished["status"] == "completed" and finished["conclusion"] == "success"
