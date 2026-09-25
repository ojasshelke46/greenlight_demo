import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let body: { runId?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with runId", 400);
  }
  if (typeof body.runId !== "string" || !validRunId(body.runId)) return detail("Missing or invalid runId", 400);
  return proxyJson(`/runs/${body.runId}/resume`, { method: "POST", body: "{}" });
}
