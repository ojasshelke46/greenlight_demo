"""The proof feature: per advisory proof that the fix closed the vulnerability, from the agent's "proof" facts,
and from the independent prover agent's child runs.

Blocks a merge while any advisory's proof test still reproduces after the fix, by the fixer's own facts or by
an independent prover. Unproven advisories, and provers still running or failed (a clone or parse error), are
allowed through; GET /features/proof/{run_id} carries their reason and status for the UI to flag.
"""

from app import facts, orchestrator
from app.features.proof.verdicts import fold_proofs
from app.hooks import Allow, ApprovalContext, CheckResult, Deny, register_approval_check

STILL_EXPLOITABLE = "Independent verification: still exploitable"


async def proof_check(ctx: ApprovalContext) -> CheckResult:
    # A late "after" fact may still be queued; never judge an irreversible merge on stale facts.
    await facts.flush()
    proofs = fold_proofs(await facts.get_facts(ctx.run.id, kind="proof"))
    blocking = [proof.reason for proof in proofs if proof.verdict == "still_exploitable"]
    return Deny("; ".join(blocking)) if blocking else Allow()


async def prover_check(ctx: ApprovalContext) -> CheckResult:
    running = orchestrator.current()
    if running is None:
        return Allow()
    verification = await running.verification(ctx.run.id)
    if any(prover["state"] == "still_exploitable" for prover in verification["provers"]):
        return Deny(STILL_EXPLOITABLE)
    return Allow()


register_approval_check(proof_check)
register_approval_check(prover_check)
