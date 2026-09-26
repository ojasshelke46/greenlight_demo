import pytest

from app import facts, hooks
from app.features.proof import proof_check
from app.features.proof.router import router
from app.features.proof.verdicts import fold_proofs
from app.hooks import Allow, ApprovalContext, Deny, PendingActionInfo, RunInfo
from tests.test_approval import FakeTrueForge, api, github_state, paused_run

R683 = "GHSA-r683-j2x4-v87g"
W7RC = "GHSA-w7rc-rwvf-8q5r"


def proof(advisory, phase, result, evidence=None):
    fact = {"kind": "proof", "phase": phase, "advisory": advisory, "result": result}
    if evidence is not None:
        fact["evidence"] = evidence
    return fact


def verdicts(fact_list):
    return {p.advisory: p.verdict for p in fold_proofs(fact_list)}


def ctx(run_id):
    return ApprovalContext(
        run=RunInfo(id=run_id, owner="acme", repo="widgets", mode="ship", via_fork=False),
        pending_action=PendingActionInfo(tool_name="merge_pull_request", arguments={"pullNumber": 7}),
        approver="ojas",
    )


# Verdicts


def test_exploitable_then_closed_is_proven_fixed():
    [result] = fold_proofs([proof(R683, "before", "exploitable", "B got Bearer token"), proof(R683, "after", "closed", "B got nothing")])
    assert result.verdict == "proven_fixed" and result.reason is None
    assert result.before.model_dump() == {"result": "exploitable", "evidence": "B got Bearer token"}
    assert result.after.model_dump() == {"result": "closed", "evidence": "B got nothing"}


def test_not_reproduced_is_unproven_with_its_reason_even_once_closed():
    [result] = fold_proofs([proof(R683, "before", "not_reproduced", "redirect never reached B"), proof(R683, "after", "closed")])
    assert result.verdict == "unproven"
    assert "could not reproduce" in result.reason and "redirect never reached B" in result.reason


def test_still_exploitable_after_the_fix_wins_over_everything():
    assert verdicts([proof(R683, "before", "exploitable"), proof(R683, "after", "still_exploitable")]) == {R683: "still_exploitable"}
    assert verdicts([proof(R683, "before", "not_reproduced"), proof(R683, "after", "still_exploitable")]) == {R683: "still_exploitable"}
    assert verdicts([proof(R683, "after", "still_exploitable")]) == {R683: "still_exploitable"}


def test_pending_until_both_phases_are_in():
    [before_only] = fold_proofs([proof(R683, "before", "exploitable")])
    assert before_only.verdict == "pending" and "after the fix" in before_only.reason
    [after_only] = fold_proofs([proof(R683, "after", "closed")])
    assert after_only.verdict == "pending" and after_only.before is None
    assert fold_proofs([]) == []


def test_a_later_result_for_the_same_phase_replaces_the_earlier_one():
    retried = [
        proof(R683, "before", "exploitable"),
        proof(R683, "after", "still_exploitable", "first attempt"),
        proof(R683, "after", "closed", "second attempt"),
    ]
    [result] = fold_proofs(retried)
    assert result.verdict == "proven_fixed" and result.after.evidence == "second attempt"


def test_malformed_proof_facts_are_ignored():
    junk = [
        {"kind": "proof", "phase": "before", "result": "exploitable"},
        proof("", "before", "exploitable"),
        proof(R683, "during", "exploitable"),
        proof(R683, "before", "closed"),
        proof(R683, "after", "exploitable"),
        proof(R683, "before", "EXPLOITABLE"),
        {"kind": "proof", "phase": "before", "advisory": 5, "result": "exploitable"},
    ]
    assert fold_proofs(junk) == []
    [result] = fold_proofs(junk + [proof(R683, "before", "exploitable", 42)])
    assert result.before.evidence is None


def test_multiple_advisories_keep_first_reported_order_and_separate_verdicts():
    mixed = [
        proof(W7RC, "before", "not_reproduced", "no oversized body to test with"),
        proof(R683, "before", "exploitable"),
        proof("GHSA-aaaa-bbbb-cccc", "before", "exploitable"),
        proof(R683, "after", "closed"),
        proof("GHSA-aaaa-bbbb-cccc", "after", "still_exploitable"),
    ]
    assert [(p.advisory, p.verdict) for p in fold_proofs(mixed)] == [
        (W7RC, "unproven"),
        (R683, "proven_fixed"),
        ("GHSA-aaaa-bbbb-cccc", "still_exploitable"),
    ]


# Approval check


def test_proof_check_is_registered_at_startup():
    assert proof_check in hooks._approval_checks


@pytest.fixture
async def ledger(tmp_path):
    from app.ledger import Ledger

    ledger = Ledger(str(tmp_path / "ledger.db"))
    await ledger.init()
    await facts.init(ledger.db)
    yield ledger
    await facts.close()
    await ledger.close()


async def test_check_denies_while_any_advisory_is_still_exploitable(ledger):
    facts.record("run_1", [proof(R683, "before", "exploitable"), proof(R683, "after", "closed")])
    facts.record("run_1", [proof(W7RC, "before", "exploitable"), proof(W7RC, "after", "still_exploitable", "B still got the header")])
    # Queued, not flushed: the check must flush before it reads.
    result = await proof_check(ctx("run_1"))
    assert isinstance(result, Deny)
    assert W7RC in result.reason and "B still got the header" in result.reason and R683 not in result.reason


async def test_check_allows_proven_unproven_pending_and_no_proof(ledger):
    await facts.add_fact("proven", proof(R683, "before", "exploitable"))
    await facts.add_fact("proven", proof(R683, "after", "closed"))
    await facts.add_fact("unproven", proof(R683, "before", "not_reproduced"))
    await facts.add_fact("pending", proof(R683, "before", "exploitable"))
    for run_id in ("proven", "unproven", "pending", "no_facts"):
        assert await proof_check(ctx(run_id)) == Allow(), run_id


async def test_check_reads_only_its_own_run(ledger):
    await facts.add_fact("other", proof(R683, "after", "still_exploitable"))
    assert await proof_check(ctx("mine")) == Allow()


# Through the API


def approve(client, run_id):
    return client.post(f"/runs/{run_id}/approval", json={"decision": "approve", "approver": "ojas"})


async def test_endpoint_returns_every_advisory_for_the_run():
    async with api(FakeTrueForge(), github_state()) as (client, app):
        run_id = await paused_run(client, app)
        assert (await client.get(f"/features/proof/{run_id}")).json() == {
            "advisories": [],
            "verification": {"status": "none", "provers": []},
        }

        facts.record(run_id, [proof(R683, "before", "exploitable", "leaked"), proof(W7RC, "before", "not_reproduced", "no big body")])
        facts.record(run_id, [proof(R683, "after", "closed", "stripped")])
        response = await client.get(f"/features/proof/{run_id}")

    assert response.status_code == 200
    assert response.json()["advisories"] == [
        {
            "advisory": R683,
            "before": {"result": "exploitable", "evidence": "leaked"},
            "after": {"result": "closed", "evidence": "stripped"},
            "verdict": "proven_fixed",
            "reason": None,
        },
        {
            "advisory": W7RC,
            "before": {"result": "not_reproduced", "evidence": "no big body"},
            "after": None,
            "verdict": "unproven",
            "reason": f"The proof test for {W7RC} could not reproduce the vulnerability before the fix, so the fix is unproven: no big body",
        },
    ]


async def test_endpoint_rejects_unknown_runs_and_missing_keys():
    async with api(FakeTrueForge(), github_state()) as (client, _):
        missing = await client.get("/features/proof/nope")
        unauthorized = await client.get("/features/proof/nope", headers={"Authorization": "Bearer wrong"})
    assert missing.status_code == 404 and missing.json() == {"detail": "Run nope not found"}
    assert unauthorized.status_code == 401
    assert router.prefix == "/features/proof"


async def test_merge_is_refused_with_the_reason_when_still_exploitable():
    fake = FakeTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        facts.record(run_id, [proof(R683, "before", "exploitable"), proof(R683, "after", "still_exploitable", "B got Bearer token")])
        response = await approve(client, run_id)
        trail = (await client.get(f"/runs/{run_id}/ledger")).json()["approvals"]

    assert response.status_code == 409
    assert response.json()["detail"] == f"The proof test for {R683} still reproduces the vulnerability after the fix: B got Bearer token"
    assert fake.resumes == []
    assert trail[0]["result"] == f"refused: {response.json()['detail']}"


async def test_merge_goes_through_when_unproven():
    fake = FakeTrueForge()
    async with api(fake, github_state()) as (client, app):
        run_id = await paused_run(client, app)
        facts.record(run_id, [proof(R683, "before", "not_reproduced", "no redirect in sandbox"), proof(R683, "after", "closed")])
        response = await approve(client, run_id)
        proofs = (await client.get(f"/features/proof/{run_id}")).json()["advisories"]

    assert response.status_code == 200, response.text
    assert len(fake.resumes) == 1
    assert proofs[0]["verdict"] == "unproven" and "no redirect in sandbox" in proofs[0]["reason"]
