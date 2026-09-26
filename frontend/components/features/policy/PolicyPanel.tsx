"use client";

import type { RunState } from "@/lib/state";
import { ApprovalPolicy } from "./ApprovalPolicy";
import { RepoRules } from "./RepoRules";

export type PolicyPanelProps = {
  variant: "run" | "approval";
  // Null where the slot is shown outside a run, e.g. the policy sheet before any run starts.
  run: RunState | null;
};

export function PolicyPanel({ variant, run }: PolicyPanelProps) {
  if (variant === "approval") return run ? <ApprovalPolicy run={run} /> : null;
  return <RepoRules repo={run?.repo ?? null} />;
}
