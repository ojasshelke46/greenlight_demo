"use client";

// finalStatus is null while the run is still going, otherwise "shipped", "release_<conclusion>" or "handed_off".
export type AuditSlotProps = { runId: string; finalStatus: string | null };

export function AuditSlot(props: AuditSlotProps) {
  void props;
  return null;
}
