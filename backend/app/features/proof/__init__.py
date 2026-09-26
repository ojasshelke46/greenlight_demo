"""The proof feature: per advisory proof that the fix closed the vulnerability, from the agent's "proof" facts.

Blocks a merge while any advisory's proof test still reproduces after the fix. Unproven advisories are
allowed through; GET /features/proof/{run_id} carries their reason for the UI to flag.
"""

from app import facts
from app.features.proof.verdicts import fold_proofs
from app.hooks import Allow, ApprovalContext, CheckResult, Deny, register_approval_check


async def proof_check(ctx: ApprovalContext) -> CheckResult:
    # A late "after" fact may still be queued; never judge an irreversible merge on stale facts.
    await facts.flush()
    proofs = fold_proofs(await facts.get_facts(ctx.run.id, kind="proof"))
    blocking = [proof.reason for proof in proofs if proof.verdict == "still_exploitable"]
    return Deny("; ".join(blocking)) if blocking else Allow()


register_approval_check(proof_check)
