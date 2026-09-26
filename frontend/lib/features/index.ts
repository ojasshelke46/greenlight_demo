// Feature state lives under RunState.features. RunModel.apply (lib/run-model.ts) calls featuresReducer
// once per stream event, with the facts that event completed, so features never parse deltas themselves.

import type { Fact } from "./markers";
import { initialPolicyState, policyReducer, type PolicyState } from "./policy";
import { initialProofState, proofReducer, type ProofState } from "./proof";

export type FeatureEvent = {
  sequence: number;
  event: Record<string, unknown>;
  // Facts whose GREENLIGHT_FACT line this event completed, parsed by lib/features/markers.ts.
  facts: Fact[];
};

export type FeaturesState = {
  proof: ProofState;
  policy: PolicyState;
};

export const initialFeaturesState: FeaturesState = {
  proof: initialProofState,
  policy: initialPolicyState,
};

export function featuresReducer(state: FeaturesState, action: FeatureEvent): FeaturesState {
  const proof = proofReducer(state.proof, action);
  const policy = policyReducer(state.policy, action);
  return proof === state.proof && policy === state.policy ? state : { proof, policy };
}
