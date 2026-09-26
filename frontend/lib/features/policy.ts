import type { FeatureEvent } from "./index";

export type PolicyState = Record<string, never>;

export const initialPolicyState: PolicyState = {};

export function policyReducer(state: PolicyState, action: FeatureEvent): PolicyState {
  void action;
  return state;
}
