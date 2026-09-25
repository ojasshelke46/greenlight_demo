import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let body: { runId?: unknown; decision?: unknown; approver?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with runId, decision and approver", 400);
  }
  const { runId, decision, approver } = body;
  if (typeof runId !== "string" || !validRunId(runId)) return detail("Missing or invalid runId", 400);
  if (decision !== "approve" && decision !== "reject") return detail("decision must be approve or reject", 400);
  if (typeof approver !== "string" || approver.trim() === "") return detail("approver is required", 400);

  // The merge checks run in the backend. This route only forwards the decision.
  return proxyJson(`/runs/${runId}/approval`, {
    method: "POST",
    body: JSON.stringify({ decision, approver: approver.trim() }),
  });
}
