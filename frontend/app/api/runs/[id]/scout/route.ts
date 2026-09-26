import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

// A scout run's ranked board as fleet rows. 409 while the scout has no result yet.
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/runs/[id]/scout">) {
  const { id } = await ctx.params;
  if (!validRunId(id)) return detail("Missing or invalid run id", 400);
  return proxyJson(`/runs/${id}/scout`);
}
