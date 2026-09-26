"use client";

import { AuditPanel } from "./AuditPanel";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>", "handed_off" or "done".
// onReport opens the ledger drawer on the auditor's run once "Generate audit report" started it.
export type AuditSlotProps = { runId: string; finalStatus: string | null; onReport?: (auditorRunId: string) => void };

export function AuditSlot({ runId, finalStatus, onReport }: AuditSlotProps) {
  if (finalStatus === null) return null;
  return <AuditPanel key={runId} runId={runId} onReport={onReport} />;
}
