"use client";

import type { RunState } from "@/lib/state";

export type ProofPanelProps = {
  variant: "run" | "approval";
  // Null where the slot is shown outside a run, e.g. the policy sheet before any run starts.
  run: RunState | null;
};

export function ProofPanel(props: ProofPanelProps) {
  void props;
  return null;
}
