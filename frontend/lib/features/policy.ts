// Repo rules from the target repo's .greenlight.yml (GET /api/features/policy), and the latest
// approval result. Neither arrives on the event stream, so policyReducer ignores stream events and is
// driven by a small shared store: the policy sheet and the approval card read the same fetch.

import { useEffect, useSyncExternalStore } from "react";
import type { ApprovalResult } from "./approval";
import type { FeatureEvent } from "./index";

export type RepoPolicy = {
  approvers: string[];
  required_approvals: number;
  allow_major_upgrades: boolean;
  freeze: { timezone: string; windows: { start: string; end: string }[] };
};

export type PolicyInfo = {
  repo: string;
  path: string;
  exists: boolean;
  // Defaults when the file is missing; null when the file is invalid.
  policy: RepoPolicy | null;
  // Set when the policy is unusable, which blocks merging.
  error: string | null;
  freeze: { active: boolean; ends_at: string | null; until: string | null };
};

export type PolicyFetch =
  | { status: "loading" }
  | { status: "ready"; info: PolicyInfo; fetchedAt: number }
  | { status: "failed"; message: string };

export type PolicyState = {
  byRepo: Record<string, PolicyFetch>;
  lastResult: ApprovalResult | null;
};

export const initialPolicyState: PolicyState = { byRepo: {}, lastResult: null };

export type PolicyAction =
  | { type: "policy/loading"; repo: string }
  | { type: "policy/loaded"; repo: string; info: PolicyInfo; at: number }
  | { type: "policy/failed"; repo: string; message: string }
  | { type: "policy/approval"; result: ApprovalResult };

export function policyReducer(state: PolicyState, action: PolicyAction | FeatureEvent): PolicyState {
  // Stream events carry nothing for the policy.
  if (!("type" in action)) return state;
  switch (action.type) {
    case "policy/loading":
      // A refresh keeps showing what was already loaded.
      return state.byRepo[action.repo]?.status === "ready" ? state : { ...state, byRepo: { ...state.byRepo, [action.repo]: { status: "loading" } } };
    case "policy/loaded":
      return { ...state, byRepo: { ...state.byRepo, [action.repo]: { status: "ready", info: action.info, fetchedAt: action.at } } };
    case "policy/failed":
      return { ...state, byRepo: { ...state.byRepo, [action.repo]: { status: "failed", message: action.message } } };
    case "policy/approval":
      return action.result === state.lastResult ? state : { ...state, lastResult: action.result };
  }
}

let state = initialPolicyState;
const listeners = new Set<() => void>();
const inflight = new Map<string, Promise<void>>();

function dispatch(action: PolicyAction): void {
  const next = policyReducer(state, action);
  if (next === state) return;
  state = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Fetch the repo's policy unless a copy newer than maxAgeMs is loaded. Concurrent calls share one request. */
export function refreshPolicy(repo: string, maxAgeMs: number): Promise<void> {
  const current = state.byRepo[repo];
  if (current?.status === "ready" && Date.now() - current.fetchedAt < maxAgeMs) return Promise.resolve();
  const running = inflight.get(repo);
  if (running) return running;

  dispatch({ type: "policy/loading", repo });
  const request = (async () => {
    try {
      const response = await fetch(`/api/features/policy?repo=${encodeURIComponent(`https://github.com/${repo}`)}`, { cache: "no-store" });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        dispatch({ type: "policy/failed", repo, message: typeof body?.detail === "string" ? body.detail : `Could not load the repo rules (${response.status})` });
      } else {
        dispatch({ type: "policy/loaded", repo, info: body as PolicyInfo, at: Date.now() });
      }
    } catch {
      dispatch({ type: "policy/failed", repo, message: "Could not reach Greenlight to load the repo rules" });
    } finally {
      inflight.delete(repo);
    }
  })();
  inflight.set(repo, request);
  return request;
}

export function recordApprovalResult(result: ApprovalResult): void {
  dispatch({ type: "policy/approval", result });
}

export function usePolicyState(): PolicyState {
  return useSyncExternalStore(subscribe, () => state, () => initialPolicyState);
}

/** The repo's policy, fetched when the caller mounts or the repo changes, reusing a copy newer than maxAgeMs. */
export function usePolicy(repo: string | null, maxAgeMs: number): PolicyFetch | null {
  const current = usePolicyState();
  useEffect(() => {
    if (repo) void refreshPolicy(repo, maxAgeMs);
  }, [repo, maxAgeMs]);
  return repo ? (current.byRepo[repo] ?? null) : null;
}

const NEED_MORE = /^(\d+) of (\d+) approvals$/;
const ORDINAL: Record<number, string> = { 2: "second", 3: "third" };

/** "1 of 2 approvals. Waiting for a second approver" from a NeedMore reason; other reasons pass through. */
export function waitingMessage(reason: string): string {
  const match = NEED_MORE.exec(reason);
  if (!match) return reason;
  const [have, need] = [Number(match[1]), Number(match[2])];
  const remaining = need - have;
  const waiting = remaining === 1 && ORDINAL[have + 1] ? `a ${ORDINAL[have + 1]} approver` : `${remaining} more approvers`;
  return `${reason}. Waiting for ${waiting}`;
}

export function sameLogin(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}
