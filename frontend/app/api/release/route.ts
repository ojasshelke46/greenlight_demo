import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("runId");
  if (!validRunId(runId)) return detail("Missing or invalid runId", 400);
  return proxyJson(`/runs/${runId}/release`);
}
