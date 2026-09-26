import type { NextRequest } from "next/server";
import { detail, proxyJson, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

// Says something to a run's agent: a new turn in the same session, or the answer to the question it asked.
export async function POST(request: NextRequest, ctx: RouteContext<"/api/runs/[id]/message">) {
  const { id } = await ctx.params;
  if (!validRunId(id)) return detail("Missing or invalid run id", 400);
  let body: { text?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with text", 400);
  }
  if (typeof body.text !== "string" || body.text.trim() === "") return detail("text is required", 400);
  return proxyJson(`/runs/${id}/message`, { method: "POST", body: JSON.stringify({ text: body.text }) });
}
