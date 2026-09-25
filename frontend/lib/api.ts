// Browser side calls. They only ever reach this app's /api routes, which proxy to the backend.

import type { AccessInfo, Mode, Release, RunSummary } from "./state";

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(", ");
  } catch {}
  return `Request failed (${response.status})`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new ApiError(await errorDetail(response), response.status);
  return response.json() as Promise<T>;
}

type BackendAccess = { repo: string; private: boolean; default_branch: string; mode_options: Mode[]; via_fork: boolean };

function toAccess(a: BackendAccess): AccessInfo {
  return {
    repo: a.repo,
    private: a.private,
    defaultBranch: a.default_branch,
    collaborator: !a.via_fork,
    viaFork: a.via_fork,
    modeOptions: a.mode_options,
  };
}

export async function fetchAccess(repoUrl: string, signal?: AbortSignal): Promise<AccessInfo> {
  return toAccess(await json<BackendAccess>(await fetch(`/api/access?repo=${encodeURIComponent(repoUrl)}`, { signal })));
}

export async function startRun(repoUrl: string, mode: Mode): Promise<{ runId: string; access: AccessInfo }> {
  const body = await json<{ run_id: string; access: BackendAccess }>(
    await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: repoUrl, mode }) }),
  );
  return { runId: body.run_id, access: toAccess(body.access) };
}

export type BackendRun = { id: string; status: string; repo: string; mode: Mode; via_fork: boolean };

export async function fetchRun(runId: string): Promise<BackendRun> {
  return json<BackendRun>(await fetch(`/api/run?runId=${encodeURIComponent(runId)}`));
}

export type Decision = { decision: "approve" | "reject"; approver: string; decided_at: string; status: string; replayed: boolean };

export async function sendDecision(runId: string, decision: "approve" | "reject", approver: string): Promise<Decision> {
  return json<Decision>(
    await fetch("/api/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId, decision, approver }) }),
  );
}

export async function fetchRelease(runId: string): Promise<Release> {
  const r = await json<{ merged: boolean; merge_commit_sha: string | null; status: string | null; conclusion: string | null; url: string | null }>(
    await fetch(`/api/release?runId=${encodeURIComponent(runId)}`),
  );
  return { merged: r.merged, mergeCommitSha: r.merge_commit_sha, status: r.status, conclusion: r.conclusion, url: r.url };
}

export type LedgerApproval = {
  tool_name: string;
  arguments: unknown;
  decision: string | null;
  approver: string | null;
  decided_at: string | null;
  result: string | null;
};

export type LedgerEvent = { sequence: number; type: string; payload: Record<string, unknown>; received_at: string };

export type Ledger = { run: { id: string; repo: string; mode: Mode; status: string; created_at: string }; events: LedgerEvent[]; approvals: LedgerApproval[] };

export async function fetchLedger(runId: string): Promise<Ledger> {
  return json<Ledger>(await fetch(`/api/ledger?runId=${encodeURIComponent(runId)}`));
}

export async function fetchAcceptedDecision(runId: string): Promise<LedgerApproval | null> {
  const ledger = await fetchLedger(runId);
  return ledger.approvals.find((a) => a.result === "accepted") ?? null;
}

type BackendRunSummary = { id: string; repo: string; mode: Mode; via_fork: boolean; status: string; created_at: string };

export async function fetchRuns(): Promise<RunSummary[]> {
  const runs = await json<BackendRunSummary[]>(await fetch("/api/runs", { cache: "no-store" }));
  return runs.map((r) => ({ id: r.id, repo: r.repo, mode: r.mode, viaFork: r.via_fork, status: r.status, createdAt: r.created_at }));
}

export async function fetchHealth(): Promise<{ ok: boolean; trueforge: boolean }> {
  const body = await json<{ ok: boolean; trueforge_reachable: boolean }>(await fetch("/api/health", { cache: "no-store" }));
  return { ok: body.ok, trueforge: body.trueforge_reachable };
}

export async function controlRun(runId: string, action: "pause" | "resume"): Promise<{ run_id: string; status: string }> {
  return json(
    await fetch(`/api/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId }) }),
  );
}
