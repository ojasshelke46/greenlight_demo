"use client";

import { AuditPanel } from "./AuditPanel";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>" or "handed_off".
export type AuditSlotProps = { runId: string; finalStatus: string | null };

export function AuditSlot({ runId, finalStatus }: AuditSlotProps) {
  if (finalStatus === null) return null;
  return <AuditPanel key={runId} runId={runId} />;
}
