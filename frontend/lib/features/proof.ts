import type { FeatureEvent } from "./index";

export type ProofState = Record<string, never>;

export const initialProofState: ProofState = {};

export function proofReducer(state: ProofState, action: FeatureEvent): ProofState {
  void action;
  return state;
}
