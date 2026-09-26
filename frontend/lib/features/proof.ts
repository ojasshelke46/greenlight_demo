// Proof of fix, per advisory, from the agent's "proof" facts. Same verdict rules as
// backend/app/features/proof/verdicts.py; keep the two in step.

import type { FeatureEvent } from "./index";
import type { Fact } from "./markers";

export type ProofVerdict = "proven_fixed" | "unproven" | "still_exploitable" | "pending";

export type ProofPhase = { result: string; evidence: string | null };

export type AdvisoryProof = {
  advisory: string;
  before: ProofPhase | null;
  after: ProofPhase | null;
  verdict: ProofVerdict;
};

export type ProofState = { advisories: AdvisoryProof[] };

export const initialProofState: ProofState = { advisories: [] };

// Results each phase may report; facts with anything else are ignored rather than guessed at.
const PHASE_RESULTS: Record<"before" | "after", ReadonlySet<string>> = {
  before: new Set(["exploitable", "not_reproduced"]),
  after: new Set(["closed", "still_exploitable"]),
};

export function proofVerdict(before: ProofPhase | null, after: ProofPhase | null): ProofVerdict {
  if (after?.result === "still_exploitable") return "still_exploitable";
  if (before?.result === "not_reproduced") return "unproven";
  if (before?.result === "exploitable" && after?.result === "closed") return "proven_fixed";
  return "pending";
}

function phaseOf(fact: Fact): { advisory: string; phase: "before" | "after"; value: ProofPhase } | null {
  const { advisory, phase, result, evidence } = fact;
  if (typeof advisory !== "string" || advisory === "") return null;
  if (phase !== "before" && phase !== "after") return null;
  if (typeof result !== "string" || !PHASE_RESULTS[phase].has(result)) return null;
  return { advisory, phase, value: { result, evidence: typeof evidence === "string" && evidence !== "" ? evidence : null } };
}

/** Folds the facts this event completed. A later result for the same phase replaces an earlier one. */
export function proofReducer(state: ProofState, action: FeatureEvent): ProofState {
  let advisories = state.advisories;
  for (const fact of action.facts) {
    if (fact.kind !== "proof") continue;
    const parsed = phaseOf(fact);
    if (!parsed) continue;

    const index = advisories.findIndex((a) => a.advisory === parsed.advisory);
    const current = index >= 0 ? advisories[index] : { advisory: parsed.advisory, before: null, after: null, verdict: "pending" as const };
    const next = { ...current, [parsed.phase]: parsed.value };
    next.verdict = proofVerdict(next.before, next.after);

    advisories = index >= 0 ? advisories.map((a, i) => (i === index ? next : a)) : [...advisories, next];
  }
  return advisories === state.advisories ? state : { advisories };
}
