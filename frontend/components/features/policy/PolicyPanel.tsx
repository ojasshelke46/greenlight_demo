"use client";

import type { RunState } from "@/lib/state";

export type PolicyPanelProps = {
  variant: "run" | "approval";
  // Null where the slot is shown outside a run, e.g. the policy sheet before any run starts.
  run: RunState | null;
};

export function PolicyPanel(props: PolicyPanelProps) {
  void props;
  return null;
}
