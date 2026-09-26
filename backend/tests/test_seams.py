import asyncio

import pytest

from app import facts, hooks
from app.hooks import (
    Allow,
    ApprovalContext,
    Deny,
    NeedMore,
    PendingActionInfo,
    RunInfo,
    build_run_message_extras,
    run_approval_checks,
)
from app.ledger import Ledger
from app.markers import FactStream, extract_facts, message_text
from tests.test_approval import FakeTrueForge, api, approvals, github_state, paused_run, wait_for_status

CTX = ApprovalContext(
    run=RunInfo(id="run_1", owner="acme", repo="widgets", mode="ship", via_fork=False),
    pending_action=PendingActionInfo(tool_name="merge_pull_request", arguments={"pullNumber": 7}),
    approver="ojas",
)


def verdict(result):
    async def check(ctx: ApprovalContext):
        return result

    return check


@pytest.fixture
def checks(monkeypatch):
    registry: list = []
    monkeypatch.setattr(hooks, "_approval_checks", registry)
    return registry


@pytest.fixture
def parts(monkeypatch):
    registry: list = []
    monkeypatch.setattr(hooks, "_run_message_parts", registry)
    return registry


# Hook ordering


async def test_no_checks_allows(checks):
    assert await run_approval_checks(CTX) == Allow()


async def test_deny_beats_need_more_beats_allow(checks):
    for result in (Allow(), NeedMore("second approver"), Deny("outside the window"), NeedMore("later"), Deny("later")):
        hooks.register_approval_check(verdict(result))
    assert await run_approval_checks(CTX) == Deny("outside the window")


async def test_first_need_more_wins_when_nothing_denies(checks):
    for result in (Allow(), NeedMore("first"), Allow(), NeedMore("second")):
        hooks.register_approval_check(verdict(result))
    assert await run_approval_checks(CTX) == NeedMore("first")


async def test_all_allow_allows(checks):
    hooks.register_approval_check(verdict(Allow()))
    hooks.register_approval_check(verdict(Allow()))
    assert await run_approval_checks(CTX) == Allow()


async def test_check_that_raises_or_returns_junk_denies(checks):
    async def broken(ctx):
        raise RuntimeError("policy file missing")

    hooks.register_approval_check(verdict(NeedMore("x")))
    hooks.register_approval_check(broken)
    assert isinstance(await run_approval_checks(CTX), Deny)

    checks.clear()
    hooks.register_approval_check(verdict(True))
    assert isinstance(await run_approval_checks(CTX), Deny)


async def test_registering_twice_keeps_one(checks):
    check = verdict(Deny("no"))
    hooks.register_approval_check(check)
    hooks.register_approval_check(check)
    assert checks == [check]


async def test_run_message_extras_join_non_null_parts_in_order(parts):
    async def none(owner, repo):
        return None

    async def broken(owner, repo):
        raise RuntimeError("boom")

    async def named(owner, repo):
        return f"Repo is {owner}/{repo}."

    async def policy(owner, repo):
        return "POLICY: two approvers"

    for part in (named, none, broken, policy):
        hooks.register_run_message_part(part)
    assert await build_run_message_extras("acme", "widgets") == "Repo is acme/widgets.\nPOLICY: two approvers"


async def test_run_message_extras_empty_without_parts(parts):
    assert await build_run_message_extras("acme", "widgets") == ""


# Marker parsing


def test_extracts_a_valid_fact():
    assert extract_facts('Done.\nGREENLIGHT_FACT {"kind": "proof", "tests": 5}') == [{"kind": "proof", "tests": 5}]


def test_skips_malformed_facts_without_raising():
    text = "\n".join(
        [
            "GREENLIGHT_FACT {not json",
            'GREENLIGHT_FACT {"tests": 5}',
            'GREENLIGHT_FACT {"kind": ""}',
            'GREENLIGHT_FACT {"kind": 3}',
            'GREENLIGHT_FACT ["kind", "proof"]',
            'GREENLIGHT_FACT"kind"',
            "GREENLIGHT_FACT ",
            'greenlight_fact {"kind": "proof"}',
            'Some text GREENLIGHT_FACT {"kind": "proof"}',
        ]
    )
    assert extract_facts(text) == []
    assert extract_facts(None) == []
    assert extract_facts("") == []


def test_extracts_several_facts_from_one_message_in_order():
    text = (
        "Upgraded node-fetch.\n"
        'GREENLIGHT_FACT {"kind": "proof", "step": 1}\n'
        "GREENLIGHT_FACT {broken\n"
        '  GREENLIGHT_FACT {"kind": "policy", "rule": "no_fork"}  \n'
        "More text\n"
        'GREENLIGHT_FACT {"kind": "proof", "step": 2}'
    )
    assert extract_facts(text) == [
        {"kind": "proof", "step": 1},
        {"kind": "policy", "rule": "no_fork"},
        {"kind": "proof", "step": 2},
    ]


def delta(message_id, content, finish_reason=None):
    return {"type": "model.message.delta", "id": message_id, "content": content, "finish_reason": finish_reason}


def feed_all(events):
    stream = FactStream()
    return [fact for event in events for fact in stream.feed(event)]


def test_fact_stream_joins_a_fact_split_across_deltas():
    events = [
        {"type": "model.message", "id": "m1", "content": None},
        delta("m1", "Tests pass.\nGREENLIGHT_FA"),
        delta("m1", 'CT {"kind": "proof", "pas'),
        delta("m1", 'sed": 12}\nmore text'),
    ]
    stream = FactStream()
    assert [stream.feed(e) for e in events] == [[], [], [], [{"kind": "proof", "passed": 12}]]


def test_fact_stream_flushes_the_last_line_at_the_end_of_a_message():
    tail = 'GREENLIGHT_FACT {"kind": "proof", "n": 1}'
    assert feed_all([delta("m1", tail), delta("m1", "", finish_reason="stop")]) == [{"kind": "proof", "n": 1}]
    assert feed_all([delta("m1", tail), {"type": "tool.response", "id": "t1"}]) == [{"kind": "proof", "n": 1}]
    assert feed_all([delta("m1", tail), delta("m2", "next message")]) == [{"kind": "proof", "n": 1}]
    assert feed_all([delta("m1", tail)]) == []


def test_fact_stream_reads_a_complete_model_message_once():
    text = 'GREENLIGHT_FACT {"kind": "proof", "n": 1}\nGREENLIGHT_FACT {"kind": "proof", "n": 2}'
    complete = {"type": "model.message", "id": "m1", "content": [{"type": "text", "text": text}]}
    assert feed_all([complete, {"type": "turn.done", "id": "d"}]) == [{"kind": "proof", "n": 1}, {"kind": "proof", "n": 2}]
    # The same message streamed as deltas and then sent complete is not counted twice.
    assert feed_all([delta("m1", text[:30]), delta("m1", text[30:]), complete]) == [{"kind": "proof", "n": 1}, {"kind": "proof", "n": 2}]


def test_fact_stream_skips_malformed_lines():
    assert feed_all([delta("m1", "GREENLIGHT_FACT {bad\nGREENLIGHT_FACT {\"no_kind\": 1}\n", finish_reason="stop")]) == []


def test_message_text_handles_every_content_shape():
    assert message_text("plain") == "plain"
    assert message_text([{"type": "text", "text": "a"}, {"type": "refusal", "refusal": "no"}, {"text": 5}, "x", {"text": "b"}]) == "ab"
    assert message_text(None) == ""


# Facts store


@pytest.fixture
async def ledger(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.db"))
    await ledger.init()
    await facts.init(ledger.db)
    yield ledger
    await facts.close()
    await ledger.close()


async def test_facts_round_trip(ledger):
    await facts.add_fact("run_1", {"kind": "proof", "tests": 5, "nested": {"ok": True}})
    await facts.add_fact("run_1", {"kind": "policy", "rule": "two_approvers"})
    await facts.add_fact("run_1", {"kind": "proof", "tests": 6})
    await facts.add_fact("run_2", {"kind": "proof", "tests": 1})

    assert await facts.get_facts("run_1") == [
        {"kind": "proof", "tests": 5, "nested": {"ok": True}},
        {"kind": "policy", "rule": "two_approvers"},
        {"kind": "proof", "tests": 6},
    ]
    assert await facts.get_facts("run_1", kind="proof") == [
        {"kind": "proof", "tests": 5, "nested": {"ok": True}},
        {"kind": "proof", "tests": 6},
    ]
    assert await facts.get_facts("run_1", kind="fleet") == []
    assert await facts.get_facts("nope") == []


async def test_record_queues_facts(ledger):
    facts.record("run_1", [{"kind": "proof", "a": 1}, {"kind": "proof", "a": 2}])
    facts.record("run_1", [])
    await facts.flush()
    assert await facts.get_facts("run_1") == [{"kind": "proof", "a": 1}, {"kind": "proof", "a": 2}]


async def test_fact_writes_never_make_ledger_writes_fail(ledger):
    """A second writer connection made the ledger drop queued events with SQLITE_BUSY."""
    await ledger.create_run(run_id="r", repo="a/b", mode="ship", via_fork=False, session_id="s", turn_id="t")
    for sequence in range(1, 400):
        ledger.append_event("r", sequence, "model.message", "{}", "t")
        if sequence % 3 == 0:
            facts.record("r", [{"kind": "k"}])
        if sequence % 5 == 0:
            await ledger.get_run("r")
            await ledger.has_event("r", "turn.done")
        await asyncio.sleep(0)
    await ledger.flush()
    await facts.flush()

    assert await ledger.last_sequence("r") == 399
    assert len(await ledger.events_after("r", 0)) == 399
    assert len(await facts.get_facts("r")) == 133


# Integration: the core calls every seam


class RecordingTrueForge(FakeTrueForge):
    def __init__(self, content=None) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.scripts["turn_1"][1]["content"] = content

    async def start_turn(self, session_id, message):
        self.messages.append(message)
        return await super().start_turn(session_id, message)


async def test_run_message_includes_registered_extras(parts):
    async def part(owner, repo):
        return f"FLEET: {owner}/{repo} is one of 3 repos"

    hooks.register_run_message_part(part)
    fake = RecordingTrueForge()
    async with api(fake, github_state()) as (client, app):
        await paused_run(client, app)

    [message] = fake.messages
    assert message.startswith("Check https://github.com/acme/widgets")
    assert message.endswith("MODE: ship\n\nFLEET: acme/widgets is one of 3 repos")


async def test_run_message_unchanged_without_extras(parts):
    fake = RecordingTrueForge()
    async with api(fake, github_state()) as (client, app):
        await paused_run(client, app)
    assert fake.messages == ["Check https://github.com/acme/widgets for vulnerable packages and fix them.\nMODE: ship"]


async def test_stream_stores_facts_from_agent_messages():
    content = [{"type": "text", "text": 'Tests pass.\nGREENLIGHT_FACT {"kind": "proof", "passed": 12}\nGREENLIGHT_FACT {oops'}]
    fake = RecordingTrueForge(content=content)
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        await facts.flush()
        stored = await facts.get_facts(run_id)
        events = (await client.get(f"/runs/{run_id}/ledger")).json()["events"]

    assert stored == [{"kind": "proof", "passed": 12}]
    assert events[1]["payload"]["content"] == content


async def test_stream_stores_facts_split_across_live_deltas():
    fake = RecordingTrueForge()
    script = fake.scripts["turn_1"]
    fragments = [delta("ev_2", "Tests pass.\nGREENLIGHT_FACT {\"ki"), delta("ev_2", 'nd": "proof", "passed": 12}')]
    fake.scripts["turn_1"] = script[:2] + fragments + script[2:]
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        await facts.flush()
        stored = await facts.get_facts(run_id)

    assert stored == [{"kind": "proof", "passed": 12}]


def approve_as(client, run_id, approver, decision="approve"):
    return client.post(f"/runs/{run_id}/approval", json={"decision": decision, "approver": approver})


async def test_deny_returns_409_and_never_resumes(checks):
    seen: list[ApprovalContext] = []

    async def window(ctx):
        seen.append(ctx)
        return Deny("Merges are frozen until Monday")

    hooks.register_approval_check(window)
    fake = RecordingTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        response = await approve_as(client, run_id, "ojas")
        trail = await approvals(client, run_id)
        run = (await client.get(f"/runs/{run_id}")).json()

    assert response.status_code == 409 and response.json()["detail"] == "Merges are frozen until Monday"
    assert fake.resumes == [] and run["status"] == "awaiting_approval"
    assert [(a["approver"], a["result"]) for a in trail] == [("ojas", "refused: Merges are frozen until Monday")]
    [ctx] = seen
    assert ctx.run == RunInfo(id=run_id, owner="acme", repo="widgets", mode="ship", via_fork=False)
    assert ctx.pending_action == PendingActionInfo(tool_name="merge_pull_request", arguments={"owner": "acme", "repo": "widgets", "pullNumber": 7})
    assert ctx.approver == "ojas" and ctx.prior_approvals == []


async def test_need_more_records_202_then_second_approver_resumes(checks):
    async def two_approvers(ctx):
        others = {p.approver for p in ctx.prior_approvals if p.result.startswith("needs_more")} - {ctx.approver}
        return Allow() if others else NeedMore("A second approver is required")

    hooks.register_approval_check(two_approvers)
    fake = RecordingTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)

        first = await approve_as(client, run_id, "ojas")
        assert first.status_code == 202, first.text
        assert first.json()["reason"] == "A second approver is required" and first.json()["replayed"] is False
        assert first.json()["status"] == "awaiting_approval"
        assert fake.resumes == []

        repeat = await approve_as(client, run_id, "ojas")
        assert repeat.status_code == 202 and repeat.json()["replayed"] is True
        assert repeat.json()["decided_at"] == first.json()["decided_at"]

        second = await approve_as(client, run_id, "deepanshu")
        assert second.status_code == 200, second.text
        assert second.json()["replayed"] is False and second.json()["reason"] is None

        await wait_for_status(app, run_id, "done")
        again = await approve_as(client, run_id, "deepanshu")
        trail = await approvals(client, run_id)

    assert len(fake.resumes) == 1
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert [(a["approver"], a["result"]) for a in trail] == [
        ("ojas", "needs_more: A second approver is required"),
        ("deepanshu", "accepted"),
    ]


async def test_reject_skips_feature_checks(checks):
    called: list[str] = []

    async def deny_everything(ctx):
        called.append(ctx.approver)
        return Deny("never")

    hooks.register_approval_check(deny_everything)
    fake = RecordingTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app, mode="ship")
        response = await approve_as(client, run_id, "ojas", decision="reject")

    assert response.status_code == 200, response.text
    assert called == [] and fake.resumes[0][2] is False
