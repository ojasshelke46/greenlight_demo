"""The helper agents wired to Greenlight: child runs and their links, triggers, the prover approval check,
the scout board, and runs created before any of this still verifying."""

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient

from app import facts
from app.config import get_settings
from app.github import GitHubClient
from app.main import create_app
from app.orchestrator import advisory_ids, parse_result, result_fact
from app.runs import RunManager
from app.trueforge import TurnEvent, TurnHandle
from tests import test_approval

AUTH = {"Authorization": "Bearer test-key"}
PR_URL = "https://github.com/acme/widgets/pull/9"
GHSA_A = "GHSA-aaaa-bbbb-cccc"
GHSA_B = "GHSA-dddd-eeee-ffff"

Script = Callable[[str], list[dict[str, Any]]]


def fact_line(kind: str, **fields: Any) -> str:
    return "GREENLIGHT_FACT " + json.dumps({"kind": kind, **fields})


def reply(text: str, status: str = "done") -> list[dict[str, Any]]:
    return [
        {"type": "turn.created", "id": "e1"},
        {"type": "model.message", "id": "e2", "content": text},
        {"type": "turn.done", "id": "e3", "state": {"status": status}},
    ]


def fixer_script(message: str) -> list[dict[str, Any]]:
    """Reports two vulnerabilities, opens a PR, and finishes."""
    pr_call = {
        "id": "call_pr",
        "type": "function",
        "function": {"name": "create_pull_request", "arguments": json.dumps({"owner": "acme", "repo": "widgets"})},
        "tool_info": {"type": "mcp", "name": "create_pull_request", "server_name": "github"},
    }
    return [
        {"type": "turn.created", "id": "e1"},
        {
            "type": "model.message",
            "id": "e2",
            "content": f"Found two.\n{fact_line('vulnerability', advisory=GHSA_A)}\n{fact_line('vulnerability', advisory=GHSA_B)}",
        },
        {"type": "model.message", "id": "e3", "content": None, "tool_calls": [pr_call]},
        {"type": "tool.response", "id": "e4", "tool_call_id": "call_pr", "content": json.dumps({"html_url": PR_URL})},
        {"type": "model.message", "id": "e5", "content": "The PR is open."},
        {"type": "turn.done", "id": "e6", "state": {"status": "done"}},
    ]


def prover_script(message: str) -> list[dict[str, Any]]:
    advisory = message.rsplit(" ", 1)[-1]
    block = json.dumps({"advisory": advisory, "before": "fail", "after": "pass", "proven": True, "comment_url": ""})
    return reply(f"PROOF BEFORE failed, PROOF AFTER passed.\n```json\n{block}\n```")


SCOUT_BOARD = {
    "repos": [
        {
            "repo": "acme/api",
            "max_severity": "high",
            "advisory_count": 3,
            "top_advisories": [{"id": GHSA_A, "package": "node-fetch", "current": "2.6.0", "fixed": "2.6.7"}],
            "skipped_reason": None,
        },
        {"repo": "acme/docs", "max_severity": "none", "advisory_count": 0, "top_advisories": [], "skipped_reason": "no package-lock.json"},
        {"repo": "acme/web", "max_severity": "critical", "advisory_count": 1, "top_advisories": [], "skipped_reason": None},
    ]
}


def auditor_script(message: str) -> list[dict[str, Any]]:
    """An auditor that tries to call a tool."""
    call = {"index": 0, "id": "call_x", "type": "function", "function": {"name": "exec", "arguments": "{}"}}
    return [
        {"type": "turn.created", "id": "e1"},
        {"type": "model.message", "id": "e2", "content": None},
        {"type": "model.message.delta", "id": "e2", "tool_calls": [call]},
        {"type": "turn.done", "id": "e3", "state": {"status": "cancelled"}},
    ]


DEFAULT_SCRIPTS: dict[str, Script] = {
    "greenlight": fixer_script,
    "greenlight-prover": prover_script,
    "greenlight-receipt": lambda message: reply("Fixed 0 advisories · 3 model calls · not available"),
    "greenlight-scout": lambda message: reply(f"Ranked.\n```json\n{json.dumps(SCOUT_BOARD)}\n```"),
    "greenlight-policy": lambda message: reply("```yaml\napprovers: [ojas]\n```"),
    "greenlight-auditor": auditor_script,
}


class AgentsFake:
    """TrueForge with one scripted agent per name. Sessions bind to the agent they were created with."""

    def __init__(self, scripts: dict[str, Script] | None = None) -> None:
        self.scripts = {**DEFAULT_SCRIPTS, **(scripts or {})}
        self.sessions: dict[str, str] = {}
        self.turn_messages: dict[str, str] = {}
        self.started: list[tuple[str, str]] = []
        # ("start" | "done", agent) in the order turns started and reached turn.done.
        self.log: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    async def get_agent_id(self, name: str | None = None) -> str:
        return f"id:{name or 'greenlight'}"

    async def create_session(self, agent_id: str) -> str:
        session_id = f"sess_{len(self.sessions) + 1}"
        self.sessions[session_id] = agent_id.removeprefix("id:")
        return session_id

    async def start_turn(self, session_id: str, message: str) -> TurnHandle:
        self.started.append((self.sessions[session_id], message))
        self.log.append(("start", self.sessions[session_id]))
        self.turn_messages[session_id] = message
        return TurnHandle(session_id=session_id, turn_id=f"turn_{session_id}", status="running")

    async def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    async def stream_turn(self, session_id: str, turn_id: str, after_sequence: int | None = None):
        agent = self.sessions[session_id]
        for sequence, event in enumerate(self.scripts[agent](self.turn_messages[session_id]), start=1):
            if sequence > (after_sequence or 0):
                await asyncio.sleep(0)
                if event["type"] == "turn.done":
                    self.log.append(("done", agent))
                yield TurnEvent(sequence=sequence, type=event["type"], data=event)

    def started_by(self, agent: str) -> list[str]:
        return [message for name, message in self.started if name == agent]


def github_handler(policy_file: str | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/widgets":
            return httpx.Response(200, json={"private": False, "default_branch": "main", "permissions": {"push": True}})
        if path == "/repos/acme/widgets/contents/.greenlight.yml":
            if policy_file is None:
                return httpx.Response(404)
            return httpx.Response(200, text=policy_file, headers={"content-type": "application/vnd.github.raw"})
        return httpx.Response(404)

    return handler


@asynccontextmanager
async def api(fake: AgentsFake, policy_file: str | None = None):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(github_handler(policy_file))) as github_http:
            app.state.github_client = GitHubClient(github_http, get_settings())
            app.state.trueforge_client = fake
            app.state.run_manager = RunManager(app.state.ledger, fake, observer=app.state.orchestrator)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH) as client:
                yield client, app


async def until(condition: Callable[[], Any], timeout: float = 5.0) -> None:
    async def poll() -> None:
        while not await condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def start_fixer(client: AsyncClient) -> str:
    response = await client.post("/runs", json={"repo": "https://github.com/acme/widgets", "mode": "pr_only"})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


async def children_of(client: AsyncClient, run_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/runs/{run_id}/children")
    assert response.status_code == 200, response.text
    return response.json()


def finished(client: AsyncClient, run_id: str, role: str, count: int) -> Callable[[], Any]:
    async def check() -> bool:
        done = [c for c in await children_of(client, run_id) if c["role"] == role and c["result"] is not None]
        return len(done) >= count

    return check


async def notes(app: Any, run_id: str, kind: str) -> list[Any]:
    return [note["payload"] for note in await app.state.ledger.get_notes(run_id, kind=kind)]


async def verify(client: AsyncClient, run_id: str) -> dict[str, Any]:
    response = await client.get(f"/runs/{run_id}/audit/verify")
    assert response.status_code == 200, response.text
    return response.json()


# Result parsing


def test_parse_result_takes_the_last_fenced_json_block():
    text = 'first\n```json\n{"a": 1}\n```\nthen\n```\n{"a": 2}\n```\n```yaml\nb: 3\n```'
    assert parse_result(text) == {"a": 2}


def test_result_fact_keeps_the_raw_text_when_parsing_fails():
    assert result_fact("prover", "no block here") == {"kind": "prover_result", "parse_error": True, "raw": "no block here"}
    assert result_fact("prover", "```json\n{broken\n```")["parse_error"] is True
    assert result_fact("scout", '```json\n{"repos": []}\n```') == {"repos": [], "kind": "scout_result"}


def test_advisory_ids_are_unique_and_ordered():
    assert advisory_ids([{"advisory": "B"}, {"advisory": "A"}, {"advisory": "B"}, {"id": "C"}, {"advisory": ""}]) == ["B", "A", "C"]


# Child runs and their links


async def test_child_run_is_linked_by_chained_notes_and_listed_under_its_parent():
    fake = AgentsFake({"greenlight": lambda message: reply("Nothing to fix.")})
    async with api(fake) as (client, app):
        parent = await start_fixer(client)
        child = await app.state.orchestrator.start_child_run(parent, "prover", f"Verify {PR_URL} for advisory X", "check X")
        await until(finished(client, parent, "prover", 1))

        assert await notes(app, parent, "run_meta") == [{"role": "fixer"}]
        links = [link for link in await notes(app, parent, "child_run") if link["role"] == "prover"]
        assert links == [{"child_run_id": child, "role": "prover", "purpose": "check X"}]
        assert await notes(app, child, "run_meta") == [{"role": "prover", "parent_run_id": parent, "purpose": "check X"}]

        listed = (await client.get("/runs")).json()
        [linked] = [c for c in await children_of(client, parent) if c["role"] == "prover"]
        run = (await client.get(f"/runs/{child}")).json()
        events = (await client.get(f"/runs/{child}/events")).text

        assert (await verify(client, parent))["ok"] is True
        assert (await verify(client, child))["ok"] is True

    assert [r["id"] for r in listed] == [parent]
    assert linked["run_id"] == child and linked["role"] == "prover" and linked["purpose"] == "check X"
    assert linked["status"] == "done"
    assert linked["result"]["after"] == "pass"
    assert run["role"] == "prover" and run["parent_run_id"] == parent and run["mode"] == "pr_only"
    assert "turn.done" in events
    assert fake.started_by("greenlight-prover") == [f"Verify {PR_URL} for advisory X"]


async def test_other_roles_are_rejected_from_the_chat():
    fake = AgentsFake()
    async with api(fake) as (client, _):
        for role in ("prover", "receipt", "auditor", "nope"):
            response = await client.post("/runs", json={"role": role, "repo": "https://github.com/acme/widgets"})
            assert response.status_code == 400, role
            assert "cannot be started from the chat" in response.json()["detail"]
        missing = await client.post("/runs", json={"role": "fixer", "repo": "https://github.com/acme/widgets"})
    assert missing.status_code == 400
    assert fake.started == []


# Triggers


async def test_pr_starts_one_prover_per_advisory_in_turn_and_never_twice():
    fake = AgentsFake()
    async with api(fake) as (client, app):
        run_id = await start_fixer(client)
        await until(finished(client, run_id, "prover", 2))
        # The trigger fires again (as it does on turn.done); both provers already exist.
        await app.state.orchestrator._start_provers(run_id, PR_URL)
        provers = [c for c in await children_of(client, run_id) if c["role"] == "prover"]

    assert fake.started_by("greenlight-prover") == [
        f"Verify {PR_URL} for advisory {GHSA_A}",
        f"Verify {PR_URL} for advisory {GHSA_B}",
    ]
    assert [p["purpose"] for p in provers] == [f"verify {PR_URL} {GHSA_A}", f"verify {PR_URL} {GHSA_B}"]
    # Sequential: the first prover's turn had finished before the second prover was started.
    assert [step for step, agent in fake.log if agent == "greenlight-prover"] == ["start", "done", "start", "done"]


async def test_no_prover_without_a_vulnerability_fact():
    def fixer(message: str) -> list[dict[str, Any]]:
        return [event for event in fixer_script(message) if event["id"] != "e2"]

    fake = AgentsFake({"greenlight": fixer})
    async with api(fake) as (client, app):
        run_id = await start_fixer(client)
        await until(finished(client, run_id, "receipt", 1))
    assert fake.started_by("greenlight-prover") == []


async def test_finished_run_gets_its_receipt_and_one_receipt_agent():
    fake = AgentsFake({"greenlight": lambda message: reply("Nothing to fix.")})
    async with api(fake) as (client, app):
        run_id = await start_fixer(client)
        await until(finished(client, run_id, "receipt", 1))
        receipt = await notes(app, run_id, "receipt")

        # A second request for the receipt, and a repeated trigger, start nothing new.
        assert (await client.get(f"/runs/{run_id}/receipt")).status_code == 200
        app.state.orchestrator.receipt_stored(run_id, receipt[0])
        await app.state.orchestrator._start_receipt_child(run_id, receipt[0])
        [child] = await children_of(client, run_id)

    assert len(receipt) == 1 and receipt[0]["source"] == "events" and receipt[0]["cost_usd"] is None
    [message] = fake.started_by("greenlight-receipt")
    assert message.startswith("Receipt data:\n") and json.loads(message.removeprefix("Receipt data:\n")) == receipt[0]
    assert child["role"] == "receipt" and child["result"]["parse_error"] is True


async def test_audit_report_starts_the_auditor_which_is_stopped_if_it_calls_a_tool():
    fake = AgentsFake({"greenlight": lambda message: reply("Nothing to fix.")})
    async with api(fake) as (client, app):
        run_id = await start_fixer(client)
        await until(finished(client, run_id, "receipt", 1))

        response = await client.post(f"/runs/{run_id}/audit/report")
        assert response.status_code == 201, response.text
        auditor = response.json()["run_id"]
        await until(finished(client, run_id, "auditor", 1))
        guard = await notes(app, auditor, "guard")
        assert (await verify(client, run_id))["ok"] is True
        assert (await verify(client, auditor))["ok"] is True

    [message] = fake.started_by("greenlight-auditor")
    report = json.loads(message.removeprefix("Audit ledger export:\n"))
    assert report["run_id"] == run_id and report["chain_verified"] is True and report["verify"]["ok"] is True
    assert all("source" not in entry for entry in report["entries"])
    assert fake.cancelled == [session for session, agent in fake.sessions.items() if agent == "greenlight-auditor"]
    assert len(guard) == 1


# Scout and policy


async def test_scout_from_the_chat_returns_its_board_as_fleet_rows():
    fake = AgentsFake()
    async with api(fake) as (client, _):
        response = await client.post("/runs", json={"role": "scout", "target": "org:acme"})
        assert response.status_code == 201, response.text
        run_id = response.json()["run_id"]
        repos = await client.post(
            "/runs", json={"role": "scout", "target": "https://github.com/acme/api, https://github.com/acme/web"}
        )

        async def has_board() -> bool:
            return (await client.get(f"/runs/{run_id}/scout")).status_code == 200

        await until(has_board)
        board = (await client.get(f"/runs/{run_id}/scout")).json()
        listed = {r["id"]: r["role"] for r in (await client.get("/runs")).json()}

    assert fake.started_by("greenlight-scout") == [
        "Scan every repo in the GitHub org acme",
        "Scan these repos: https://github.com/acme/api, https://github.com/acme/web",
    ]
    assert listed[run_id] == "scout" and listed[repos.json()["run_id"]] == "scout"
    assert board["target"] == "org:acme"
    assert [r["repo"] for r in board["repos"]] == ["acme/web", "acme/api", "acme/docs"]
    api_row = board["repos"][1]
    assert api_row["counts"] == {"critical": 0, "high": 1, "moderate": 0, "low": 0, "unknown": 2}
    assert api_row["top"] == [{"id": GHSA_A, "package": "node-fetch", "version": "2.6.0", "severity": "unknown", "summary": "Fixed in 2.6.7"}]
    assert board["repos"][2]["status"] == "no_lockfile"
    assert board["totals"]["repos"] == 3 and board["totals"]["no_lockfile"] == 1


def test_scout_scan_failures_are_errors_not_missing_lockfiles():
    from app.features.fleet.models import scout_board

    board = scout_board("user:ojas", {"repos": [
        {"repo": "o/a", "max_severity": "none", "advisory_count": 0, "skipped_reason": "scan failed: sandbox unavailable, package-lock.json present"},
        {"repo": "o/b", "max_severity": "none", "advisory_count": 0, "skipped_reason": "no package-lock.json (Python project)"},
    ]})
    rows = {r.repo: r for r in board.repos}
    assert rows["o/a"].status == "error" and "sandbox unavailable" in rows["o/a"].error
    assert rows["o/b"].status == "no_lockfile" and rows["o/b"].error is None


async def test_scout_board_for_a_reply_without_json_is_an_error_not_a_guess():
    fake = AgentsFake({"greenlight-scout": lambda message: reply("I could not finish.")})
    async with api(fake) as (client, _):
        run_id = (await client.post("/runs", json={"role": "scout", "target": "user:ojas"})).json()["run_id"]

        async def settled() -> bool:
            return (await client.get(f"/runs/{run_id}/scout")).status_code != 409

        await until(settled)
        response = await client.get(f"/runs/{run_id}/scout")
    assert response.status_code == 502


async def test_policy_draft_only_for_a_repo_without_a_policy_file():
    fake = AgentsFake()
    async with api(fake) as (client, _):
        response = await client.post("/features/policy/draft", params={"repo": "https://github.com/acme/widgets"})
        assert response.status_code == 201, response.text
        chat = await client.post("/runs", json={"role": "policy", "repo": "https://github.com/acme/widgets"})
    async with api(AgentsFake(), policy_file="approvers: [ojas]\n") as (client, _):
        refused = await client.post("/features/policy/draft", params={"repo": "https://github.com/acme/widgets"})

    assert chat.status_code == 201
    assert fake.started_by("greenlight-policy") == ["Draft a .greenlight.yml for https://github.com/acme/widgets"] * 2
    assert refused.status_code == 409


# Prover approval check


async def add_prover(app: Any, parent: str, result: dict[str, Any] | None, status: str = "done") -> str:
    """A prover child as the orchestrator records one, without running it."""
    child = f"prover-{len(await app.state.ledger.get_notes(parent, kind='child_run'))}"
    ledger = app.state.ledger
    await ledger.create_run(run_id=child, repo="acme/widgets", mode="pr_only", via_fork=False, session_id="s", turn_id="t")
    ledger.set_status(child, status)
    await ledger.flush()
    purpose = f"verify {PR_URL} {GHSA_A}"
    await ledger.append_note(child, "run_meta", {"role": "prover", "parent_run_id": parent, "purpose": purpose})
    await ledger.append_note(parent, "child_run", {"child_run_id": child, "role": "prover", "purpose": purpose})
    if result is not None:
        await facts.add_fact(child, {**result, "kind": "prover_result"})
    return child


async def test_prover_that_still_fails_after_the_fix_blocks_the_merge():
    fake = test_approval.FakeTrueForge()
    async with test_approval.api(fake, test_approval.github_state()) as (client, app):
        run_id = await test_approval.paused_run(client, app)
        await add_prover(app, run_id, {"advisory": GHSA_A, "before": "fail", "after": "fail", "proven": False})
        response = await client.post(f"/runs/{run_id}/approval", json={"decision": "approve", "approver": "ojas"})
        proof = (await client.get(f"/features/proof/{run_id}")).json()

    assert response.status_code == 409
    assert response.json()["detail"] == "Independent verification: still exploitable"
    assert fake.resumes == []
    assert proof["verification"]["status"] == "still_exploitable"


async def test_running_or_broken_provers_are_shown_but_never_block():
    fake = test_approval.FakeTrueForge()
    async with test_approval.api(fake, test_approval.github_state()) as (client, app):
        run_id = await test_approval.paused_run(client, app)
        await add_prover(app, run_id, None, status="running")
        await add_prover(app, run_id, {"parse_error": True, "raw": "git clone failed"})
        await add_prover(app, run_id, {"advisory": GHSA_A, "before": "error", "after": "error", "proven": False})
        proof = (await client.get(f"/features/proof/{run_id}")).json()
        response = await client.post(f"/runs/{run_id}/approval", json={"decision": "approve", "approver": "ojas"})

    assert [p["state"] for p in proof["verification"]["provers"]] == ["running", "error", "error"]
    assert proof["verification"]["status"] == "running"
    assert response.status_code == 200, response.text
    assert len(fake.resumes) == 1


# Runs created before this change


async def test_run_created_before_child_runs_still_verifies_and_lists_as_a_fixer_run():
    fake = AgentsFake()
    async with api(fake) as (client, app):
        ledger = app.state.ledger
        # Exactly what the backend wrote before run_meta notes existed.
        await ledger.create_run(run_id="legacy", repo="acme/widgets", mode="ship", via_fork=False, session_id="s", turn_id="t")
        ledger.append_event("legacy", 1, "turn.created", json.dumps({"type": "turn.created"}))
        ledger.append_event("legacy", 2, "turn.done", json.dumps({"type": "turn.done", "state": {"status": "done"}}))
        ledger.set_status("legacy", "done")
        await ledger.flush()
        await ledger.append_note("legacy", "receipt", {"model_calls": 0})

        before = await verify(client, "legacy")
        run = (await client.get("/runs/legacy")).json()
        listed = (await client.get("/runs")).json()
        auditor = (await client.post("/runs/legacy/audit/report")).json()["run_id"]
        await until(finished(client, "legacy", "auditor", 1))
        after = await verify(client, "legacy")

    assert before["ok"] is True and after["ok"] is True
    assert after["entries"] == before["entries"] + 1  # the child_run note
    assert run["role"] == "fixer" and run["parent_run_id"] is None
    assert [(r["id"], r["role"]) for r in listed] == [("legacy", "fixer")]
    assert auditor
