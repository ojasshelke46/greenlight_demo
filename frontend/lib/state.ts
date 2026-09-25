// The whole UI renders from one GreenlightState. Today it is placeholder data held in useState;
// later a reducer over real TrueForge events (via the FastAPI backend) replaces it with the same shape.

export type Signal = "red" | "amber" | "green";
export type Mode = "ship" | "pr_only";

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
  | { status: "invalid" }
  | { status: "checking" }
  | { status: "ready"; access: AccessInfo }
  | { status: "error"; message: string };

export type LaunchState = {
  repoUrl: string;
  access: AccessState;
  mode: Mode | null;
};

export type StepId =
  | "scan"
  | "sandbox"
  | "baseline"
  | "upgrade"
  | "fix"
  | "pull_request"
  | "green_light"
  | "shipped"
  | "handed_off";

export type StepStatus = "waiting" | "active" | "done" | "blocked" | "needs_you";

export type Step = {
  id: StepId;
  label: string;
  status: StepStatus;
  detail?: string;
  detailSignal?: Signal;
};

export type TerminalLine = {
  id: number;
  kind: "command" | "output" | "pass" | "fail" | "agent";
  text: string;
};

export type Vulnerability = {
  advisory: string;
  packageName: string;
  installed: string;
  patched: string;
  severity: string;
  summary: string;
  status: "open" | "fixing" | "fixed";
};

export type DiffLine = { kind: "add" | "remove" | "context"; text: string };
export type DiffFile = { path: string; hunks: { header: string; lines: DiffLine[] }[] };

export type PolicyAction = { label: string; tool?: string; note?: string; pending?: boolean };
export type PolicyTier = { id: "auto" | "approval" | "never"; title: string; actions: PolicyAction[] };

export type RunStatus = {
  signal: Signal;
  label: string;
  detail: string;
};

export type RunState = {
  id: string;
  repo: string;
  mode: Mode;
  viaFork: boolean;
  private: boolean;
  defaultBranch: string;
  connection: "live" | "reconnecting" | "ended";
  status: RunStatus;
  steps: Step[];
  terminal: TerminalLine[];
  vulnerabilities: Vulnerability[];
  diff: DiffFile[];
  policy: PolicyTier[];
};

export type GreenlightState = {
  view: "launch" | "run";
  launch: LaunchState;
  run: RunState | null;
};

export const MODE_LABEL: Record<Mode, string> = {
  ship: "Ship it",
  pr_only: "PR only",
};

export function accessLabel(run: Pick<RunState, "viaFork">): string {
  return run.viaFork ? "Via fork" : "Collaborator";
}

const REPO_URL = /^https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]+?)(?:\.git)?\/?$/;

export function parseRepoUrl(url: string): string | null {
  const match = REPO_URL.exec(url.trim());
  return match ? `${match[1]}/${match[2]}` : null;
}
