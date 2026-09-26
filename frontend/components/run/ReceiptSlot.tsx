"use client";

import type { ChildRun } from "@/lib/state";
import { Receipt } from "./Receipt";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>", "handed_off" or "done".
// breakdown is the receipt agent's run, once Greenlight has started it.
export type ReceiptSlotProps = { runId: string; finalStatus: string | null; breakdown?: ChildRun | null };

export function ReceiptSlot({ runId, finalStatus, breakdown = null }: ReceiptSlotProps) {
  if (finalStatus === null) return null;
  return <Receipt key={runId} runId={runId} breakdown={breakdown} />;
}
