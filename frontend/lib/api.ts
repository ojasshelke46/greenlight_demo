// Browser side calls. They only ever reach this app's /api routes, which proxy to the backend.

import type { Summary as FleetSummary } from "@/components/features/fleet/sweep";
import type { AccessInfo, AgentInfo, ChatRole, ChildRun, Mode, Release, Role, RunSummary } from "./state";

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

export type StartRequest =
  | { role: "fixer"; repo: string; mode: Mode }
  | { role: "scout"; target: string }
  | { role: "policy"; repo: string };

export async function startAgentRun(request: StartRequest): Promise<{ runId: string; role: ChatRole; access: AccessInfo | null }> {
  const body = await json<{ run_id: string; role: ChatRole; access: BackendAccess | null }>(
    await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) }),
  );
  return { runId: body.run_id, role: body.role, access: body.access ? toAccess(body.access) : null };
}

export async function startRun(repoUrl: string, mode: Mode): Promise<{ runId: string; access: AccessInfo }> {
  const started = await startAgentRun({ role: "fixer", repo: repoUrl, mode });
  if (!started.access) throw new ApiError("The backend did not return repo access for this run", 502);
  return { runId: started.runId, access: started.access };
}

export type BackendRun = {
  id: string;
  status: string;
  repo: string;
  mode: Mode;
  via_fork: boolean;
  role: Role;
  parent_run_id: string | null;
  task: "scan" | "fix" | "policy" | null;
  campaign_id: string | null;
  package: string | null;
  pr_url: string | null;
};

export async function fetchRun(runId: string): Promise<BackendRun> {
  return json<BackendRun>(await fetch(`/api/run?runId=${encodeURIComponent(runId)}`));
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

type BackendRunSummary = { id: string; repo: string; mode: Mode; via_fork: boolean; status: string; created_at: string; role?: Role };

export async function fetchRuns(): Promise<RunSummary[]> {
  const runs = await json<BackendRunSummary[]>(await fetch("/api/runs", { cache: "no-store" }));
  return runs.map((r) => ({ id: r.id, role: r.role ?? "fixer", repo: r.repo, mode: r.mode, viaFork: r.via_fork, status: r.status, createdAt: r.created_at }));
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  return json<AgentInfo[]>(await fetch("/api/agents", { cache: "no-store" }));
}

type BackendChild = { run_id: string; role: Role; purpose: string | null; status: string; created_at: string; result: Record<string, unknown> | null };

export async function fetchChildren(runId: string, signal?: AbortSignal): Promise<ChildRun[]> {
  const children = await json<BackendChild[]>(await fetch(`/api/runs/${encodeURIComponent(runId)}/children`, { cache: "no-store", signal }));
  return children.map((c) => ({ runId: c.run_id, role: c.role, purpose: c.purpose, status: c.status, createdAt: c.created_at, result: c.result }));
}

/** The scout's board, or null while the scout has no result yet (409). */
export async function fetchScoutBoard(runId: string, signal?: AbortSignal): Promise<FleetSummary | null> {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/scout`, { cache: "no-store", signal });
  if (response.status === 409) return null;
  return json<FleetSummary>(response);
}

/** Starts the auditor on this run's export. Returns the auditor's own run id. */
export async function requestAuditReport(runId: string): Promise<string> {
  const body = await json<{ run_id: string }>(
    await fetch("/api/audit/report", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId }) }),
  );
  return body.run_id;
}

/** Starts the policy agent drafting a .greenlight.yml. Returns the new top level policy run id. */
export async function draftPolicy(repoUrl: string): Promise<string> {
  const body = await json<{ run_id: string }>(
    await fetch("/api/policy/draft", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo: repoUrl }) }),
  );
  return body.run_id;
}

export type ProverState = "running" | "proven" | "still_exploitable" | "error" | "inconclusive";
export type Verification = {
  status: "none" | ProverState;
  provers: { run_id: string; advisory: string | null; run_status: string; state: ProverState; result: Record<string, unknown> | null }[];
};

export async function fetchVerification(runId: string, signal?: AbortSignal): Promise<Verification> {
  const body = await json<{ verification: Verification }>(await fetch(`/api/features/proof/${encodeURIComponent(runId)}`, { cache: "no-store", signal }));
  return body.verification;
}

export async function fetchHealth(): Promise<{ ok: boolean; trueforge: boolean }> {
  const body = await json<{ ok: boolean; trueforge_reachable: boolean }>(await fetch("/api/health", { cache: "no-store" }));
  return { ok: body.ok, trueforge: body.trueforge_reachable };
}

/** Says something to the run's agent in the same session, or answers the question it asked. */
export async function sendMessage(runId: string, text: string): Promise<{ run_id: string; status: string }> {
  return json(
    await fetch(`/api/runs/${encodeURIComponent(runId)}/message`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }),
  );
}

export async function controlRun(runId: string, action: "pause" | "resume"): Promise<{ run_id: string; status: string }> {
  return json(
    await fetch(`/api/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ runId }) }),
  );
}
