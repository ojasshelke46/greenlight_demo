import type { NextRequest } from "next/server";
import { backendHeaders, backendUrl, detail, validRunId } from "@/lib/backend";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("runId");
  if (!validRunId(runId)) return detail("Missing or invalid runId", 400);

  // EventSource sends Last-Event-ID on reconnect; a fresh connection can pass ?after= instead.
  const lastEventId = request.headers.get("Last-Event-ID") ?? request.nextUrl.searchParams.get("after");
  const headers = backendHeaders({ Accept: "text/event-stream" });
  if (lastEventId && /^\d+$/.test(lastEventId)) headers.set("Last-Event-ID", lastEventId);

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/runs/${runId}/events`), {
      headers,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return detail("The Greenlight backend is not reachable. Is it running on BACKEND_URL?", 502);
  }

  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
    });
  }

  // Pass the body straight through; no buffering, so each event reaches the browser as it arrives.
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
