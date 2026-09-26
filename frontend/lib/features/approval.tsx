"use client";

// The one place approvals are sent from. Features read who is approving and what the backend said,
// including the reason behind a 202 (more is needed) or a 409 (denied).

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type ApprovalResult =
  | { kind: "decided"; runId: string; decision: "approve" | "reject"; approver: string; decidedAt: string; replayed: boolean }
  | { kind: "need_more"; runId: string; decision: "approve" | "reject"; approver: string; decidedAt: string; replayed: boolean; reason: string }
  | { kind: "denied"; runId: string; reason: string }
  | { kind: "error"; runId: string; status: number | null; reason: string };

type ApprovalValue = {
  approver: string;
  setApprover: (approver: string) => void;
  lastResult: ApprovalResult | null;
  submitApproval: (runId: string, decision: "approve" | "reject") => Promise<ApprovalResult>;
};

const ApprovalContext = createContext<ApprovalValue | null>(null);

type DecisionBody = { decision?: "approve" | "reject"; approver?: string; decided_at?: string; replayed?: boolean; reason?: string | null; detail?: unknown };

function reasonOf(body: DecisionBody | null, status: number): string {
  if (typeof body?.reason === "string" && body.reason) return body.reason;
  if (typeof body?.detail === "string") return body.detail;
  return `Request failed (${status})`;
}

async function post(runId: string, decision: "approve" | "reject", approver: string): Promise<ApprovalResult> {
  let response: Response;
  try {
    response = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runId, decision, approver }),
    });
  } catch {
    return { kind: "error", runId, status: null, reason: "Could not reach Greenlight" };
  }
  const body = (await response.json().catch(() => null)) as DecisionBody | null;
  const recorded = {
    runId,
    decision: body?.decision ?? decision,
    approver: body?.approver ?? approver,
    decidedAt: body?.decided_at ?? "",
    replayed: Boolean(body?.replayed),
  };

  if (response.status === 202) return { kind: "need_more", ...recorded, reason: reasonOf(body, 202) };
  if (response.ok) return { kind: "decided", ...recorded };
  if (response.status === 409) return { kind: "denied", runId, reason: reasonOf(body, 409) };
  return { kind: "error", runId, status: response.status, reason: reasonOf(body, response.status) };
}

export function ApprovalProvider({ children, defaultApprover = "demo" }: { children: ReactNode; defaultApprover?: string }) {
  const [approver, setApprover] = useState(defaultApprover);
  const [lastResult, setLastResult] = useState<ApprovalResult | null>(null);

  const submitApproval = useCallback(
    async (runId: string, decision: "approve" | "reject") => {
      const result = await post(runId, decision, approver);
      setLastResult(result);
      return result;
    },
    [approver],
  );

  const value = useMemo(() => ({ approver, setApprover, lastResult, submitApproval }), [approver, lastResult, submitApproval]);
  return <ApprovalContext.Provider value={value}>{children}</ApprovalContext.Provider>;
}

export function useApproval(): ApprovalValue {
  const value = useContext(ApprovalContext);
  if (!value) throw new Error("useApproval must be used inside ApprovalProvider");
  return value;
}
