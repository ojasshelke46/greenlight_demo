import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let body: { repo?: unknown; mode?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with repo and mode", 400);
  }
  if (typeof body.repo !== "string" || (body.mode !== "ship" && body.mode !== "pr_only")) {
    return detail("Body must be JSON with repo and mode", 400);
  }
  return proxyJson("/runs", { method: "POST", body: JSON.stringify({ repo: body.repo, mode: body.mode }) });
}

// Used to restore a run after a page reload.
export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("runId");
  if (!validRunId(runId)) return detail("Missing or invalid runId", 400);
  return proxyJson(`/runs/${runId}`);
}
