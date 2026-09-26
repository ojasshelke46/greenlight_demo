import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

// The helper agent runs a run started: role, purpose, status, and the parsed result once there is one.
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/runs/[id]/children">) {
  const { id } = await ctx.params;
  if (!validRunId(id)) return detail("Missing or invalid run id", 400);
  return proxyJson(`/runs/${id}/children`);
}
