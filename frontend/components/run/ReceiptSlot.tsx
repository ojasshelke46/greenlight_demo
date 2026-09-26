"use client";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>" or "handed_off".
export type ReceiptSlotProps = { runId: string; finalStatus: string | null };

export function ReceiptSlot(props: ReceiptSlotProps) {
  void props;
  return null;
}
