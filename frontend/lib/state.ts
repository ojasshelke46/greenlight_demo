// Everything on screen renders from these types. Launch data comes from GET /api/access; a run is a
// projection of real TrueForge events streamed through the backend (see lib/run-model.ts).

// Status tones appear only inside agent output: fail (vulnerable or failing), progress (in progress),
// done (passing or done).
import type { FeaturesState } from "./features";

export type Tone = "fail" | "progress" | "done";
export type Mode = "ship" | "pr_only";

// TrueForge agents by role. Fixer, scout and policy start from the chat; the rest are started by Greenlight.
export type Role = "fixer" | "prover" | "policy" | "scout" | "receipt" | "auditor";
export type ChatRole = "fixer" | "scout" | "policy";
export const CHAT_ROLES: ChatRole[] = ["fixer", "scout", "policy"];

export const ROLE_LABEL: Record<Role, string> = {
  fixer: "Fix",
  prover: "Prover",
  policy: "Policy",
  scout: "Scout",
  receipt: "Receipt",
  auditor: "Auditor",
};

export type AgentInfo = { role: Role; name: string; model: string | null; found: boolean; chat: boolean };

/** A helper agent run started by a run, as GET /api/runs/<id>/children reports it. */
export type ChildRun = {
  runId: string;
  role: Role;
  purpose: string | null;
  // running, awaiting_approval, awaiting_input, done, error, cancelled, resume_failed, or missing
  status: string;
  createdAt: string;
  // The helper's parsed final json block, or {parse_error: true, raw} when it had none.
  result: Record<string, unknown> | null;
};

export type AccessInfo = {
  repo: string;
  private: boolean;
  defaultBranch: string;
  collaborator: boolean;
  viaFork: boolean;
  modeOptions: Mode[];
};

export type AccessState =
  | { status: "idle" }
  | { status: "checking"; repo: string }
  | { status: "ready"; access: AccessInfo }
  | { status: "error"; message: string };

export type StepId =
  | "scan"
  | "sandbox"
  | "baseline"
  | "upgrade"
  | "fix"
  | "pull_request"
  | "awaiting_approval"
  | "shipped"
  | "handed_off";

export type StepStatus = "waiting" | "active" | "done" | "failed" | "paused";

export type Step = {
  id: StepId;
  label: string;
  status: StepStatus;
  detail?: string;
  tone?: Tone;
  startedAt?: number;
  endedAt?: number;
};

export type TerminalLine = {
  id: string;
  kind: "output" | "pass" | "fail";
  text: string;
};

export type TerminalCommand = {
  id: string;
  command: string;
  intent: string | null;
  running: boolean;
  // Never answered: its turn ended (paused, cancelled or errored) before the command returned.
  stopped: boolean;
  ok: boolean | null;
  exitCode: number | null;
  lines: TerminalLine[];
};

export type Vulnerability = {
  advisory: string;
  packageName: string;
  affected: string;
  patched: string;
  severity: string;
  summary: string;
  url?: string;
  status: "open" | "fixing" | "fixed";
};

export type DiffLine = { kind: "add" | "remove" | "context"; text: string };
export type DiffFile = { path: string; hunks: { header: string; lines: DiffLine[] }[] };

export type PullRequest = {
  number: number | null;
  url: string | null;
  title: string | null;
  branch: string | null;
  base: string | null;
  fromFork: boolean;
};

export type Block = (
  | { kind: "text"; id: string; text: string }
  // What the user said in a later turn of the same session: a message, an answer, or the try again prompt.
  | { kind: "user"; id: string; text: string; answer: boolean }
  | { kind: "terminal"; id: string; commands: TerminalCommand[] }
  | { kind: "action"; id: string; server: string | null; tool: string; target: string; running: boolean; stopped: boolean; error: string | null }
  | { kind: "vulns"; id: string; items: Vulnerability[] }
  | { kind: "diff"; id: string; files: DiffFile[] }
  | { kind: "pr"; id: string; pr: PullRequest }
) & {
  // When the agent message that produced the block started, in ms since epoch.
  at?: number;
};

export type RunStatus = {
  tone: Tone;
  // True only while the agent is actually executing; a paused or finished run is still.
  working: boolean;
  label: string;
  detail: string;
};

export type PendingApproval = {
  tool: string;
  server: string | null;
  arguments: Record<string, unknown> | null;
  pullNumber: number | null;
  pullUrl: string | null;
};

export type ApprovalContext = {
  filesChanged: string[];
  testSummary: string | null;
  rollback: string;
  rollbackFromAgent: boolean;
};

export type ApprovalState =
  | { status: "none" }
  | { status: "pending"; pending: PendingApproval }
  | { status: "sending"; pending: PendingApproval; decision: "approve" | "reject" }
  | { status: "refused"; pending: PendingApproval; reason: string }
  | { status: "decided"; decision: "approve" | "reject"; approver: string; decidedAt: string };

export type Release = {
  merged: boolean;
  mergeCommitSha: string | null;
  status: string | null;
  conclusion: string | null;
  url: string | null;
};

export type RunMeta = {
  id: string;
  role: Role;
  // Campaign runs: "scan" is the campaign itself (its id is the campaign id), "fix" fixes one package.
  task?: "scan" | "fix" | "policy" | null;
  campaignId?: string | null;
  package?: string | null;
  repo: string;
  mode: Mode;
  viaFork: boolean;
  private: boolean | null;
  defaultBranch: string | null;
};

export type Failure = { stepId: StepId | null; explanation: string | null; reason: string };

/** The agent paused to ask the user something (tool.response_required on ask_user_question). */
export type AgentQuestion = { text: string; options: string[] };

export type RunState = RunMeta & {
  connection: "connecting" | "live" | "paused" | "reconnecting" | "ended";
  eventCount: number;
  lastSequence: number;
  status: RunStatus;
  steps: Step[];
  blocks: Block[];
  approval: ApprovalState;
  approvalContext: ApprovalContext | null;
  pullRequest: PullRequest | null;
  merged: boolean;
  release: Release | null;
  failure: Failure | null;
  question: AgentQuestion | null;
  finished: boolean;
  // Paused by the user (TrueForge turn cancelled with reason client-cancelled).
  paused: boolean;
  // A pause request was sent and TrueForge has not ended the turn yet.
  pausing: boolean;
  // An ended run (paused, errored, or stopped short) can continue with a new turn in the same session.
  canResume: boolean;
  // Owned by the features in lib/features; folded from the same events as everything above.
  features: FeaturesState;
};

export type RunSummary = {
  id: string;
  // A run, or a fleet or policy campaign listed with the runs.
  kind?: "run" | "fleet" | "policy";
  role: Role;
  repo: string;
  mode: Mode;
  viaFork: boolean;
  status: string;
  createdAt: string;
};

export const MODE_LABEL: Record<Mode, string> = {
  ship: "Ship it",
  pr_only: "PR only",
};

const REPO_URL = /https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]+?)(?:\.git)?(?=[/\s?#]|$)/;

/** The first GitHub repo link inside free text, normalised to https://github.com/owner/repo. */
export function findRepoUrl(text: string): { url: string; repo: string } | null {
  const match = REPO_URL.exec(text);
  if (!match) return null;
  const repo = `${match[1]}/${match[2]}`;
  return { url: `https://github.com/${repo}`, repo };
}
