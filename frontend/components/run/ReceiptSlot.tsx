"use client";

import { Receipt } from "./Receipt";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>" or "handed_off".
export type ReceiptSlotProps = { runId: string; finalStatus: string | null };

export function ReceiptSlot({ runId, finalStatus }: ReceiptSlotProps) {
  if (finalStatus === null) return null;
  return <Receipt key={runId} runId={runId} />;
}
