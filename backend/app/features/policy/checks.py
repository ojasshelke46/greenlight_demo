from datetime import datetime, timezone

from app.approvals import NEEDS_MORE_PREFIX
from app.features.policy.loader import load_policy
from app.features.policy.model import active_freeze
from app.hooks import Allow, ApprovalContext, CheckResult, Deny, NeedMore

MAJORS_DISALLOWED = "POLICY: allow_major_upgrades=false"


def now() -> datetime:
    return datetime.now(timezone.utc)


async def policy_check(ctx: ApprovalContext) -> CheckResult:
    loaded = await load_policy(ctx.run.owner, ctx.run.repo)
    if loaded.error is not None or loaded.policy is None:
        return Deny(loaded.error or f"The policy in {loaded.path} could not be loaded")
    policy = loaded.policy

    if not policy.allows(ctx.approver):
        return Deny(f"{ctx.approver} is not an allowed approver. Allowed approvers: {', '.join(policy.approvers)}")

    freeze = active_freeze(policy.freeze, now())
    if freeze is not None:
        return Deny(f"Merges frozen until {freeze.until}")

    # Approvals that stood: accepted, or recorded while more were needed. Refusals never count.
    approvers = {
        prior.approver.casefold()
        for prior in ctx.prior_approvals
        if prior.decision == "approve"
        and (prior.result == "accepted" or prior.result.startswith(NEEDS_MORE_PREFIX))
        and policy.allows(prior.approver)
    }
    approvers.add(ctx.approver.casefold())
    if len(approvers) < policy.required_approvals:
        return NeedMore(f"{len(approvers)} of {policy.required_approvals} approvals")
    return Allow()


async def majors_message_part(owner: str, repo: str) -> str | None:
    loaded = await load_policy(owner, repo)
    if loaded.policy is not None and not loaded.policy.allow_major_upgrades:
        return MAJORS_DISALLOWED
    return None
