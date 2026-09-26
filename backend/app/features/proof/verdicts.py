"""Per advisory proof verdicts, folded from the agent's "proof" facts in the order they were reported."""

from typing import Any, Literal

from pydantic import BaseModel

Verdict = Literal["proven_fixed", "unproven", "still_exploitable", "pending"]

# Results each phase may report; facts with anything else are ignored rather than guessed at.
PHASE_RESULTS = {"before": {"exploitable", "not_reproduced"}, "after": {"closed", "still_exploitable"}}


class PhaseResult(BaseModel):
    result: str
    evidence: str | None


class AdvisoryProof(BaseModel):
    advisory: str
    before: PhaseResult | None
    after: PhaseResult | None
    verdict: Verdict
    # Why the verdict is not proven_fixed, in words the UI can show; None when it is proven.
    reason: str | None


def _evidence(fact: dict[str, Any]) -> str | None:
    evidence = fact.get("evidence")
    return evidence if isinstance(evidence, str) and evidence else None


def _verdict(advisory: str, before: PhaseResult | None, after: PhaseResult | None) -> tuple[Verdict, str | None]:
    if after is not None and after.result == "still_exploitable":
        detail = f": {after.evidence}" if after.evidence else ""
        return "still_exploitable", f"The proof test for {advisory} still reproduces the vulnerability after the fix{detail}"
    if before is not None and before.result == "not_reproduced":
        detail = f": {before.evidence}" if before.evidence else ""
        return "unproven", f"The proof test for {advisory} could not reproduce the vulnerability before the fix, so the fix is unproven{detail}"
    if before is not None and before.result == "exploitable" and after is not None and after.result == "closed":
        return "proven_fixed", None
    if before is None:
        return "pending", f"No proof test result for {advisory} yet"
    return "pending", f"Waiting for the proof test for {advisory} to run again after the fix"


def fold_proofs(facts: list[dict[str, Any]]) -> list[AdvisoryProof]:
    """One entry per advisory, in first reported order. A later fact for the same phase replaces an earlier one."""
    phases: dict[str, dict[str, PhaseResult]] = {}
    for fact in facts:
        advisory, phase, result = fact.get("advisory"), fact.get("phase"), fact.get("result")
        if not isinstance(advisory, str) or not advisory or phase not in PHASE_RESULTS or result not in PHASE_RESULTS[phase]:
            continue
        phases.setdefault(advisory, {})[phase] = PhaseResult(result=result, evidence=_evidence(fact))

    proofs = []
    for advisory, seen in phases.items():
        before, after = seen.get("before"), seen.get("after")
        verdict, reason = _verdict(advisory, before, after)
        proofs.append(AdvisoryProof(advisory=advisory, before=before, after=after, verdict=verdict, reason=reason))
    return proofs
