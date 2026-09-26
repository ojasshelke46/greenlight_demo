import type { NextRequest } from "next/server";
import { backendHeaders, backendUrl, detail } from "@/lib/backend";

// /api/campaigns/<path> -> {BACKEND_URL}/campaigns/<path>, with the API key added here, server side.
// Covers repo, fleet and policy campaigns, including the fleet's event stream.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Package names can be scoped (@scope/name), so a segment may hold @ but never . or .. on its own.
const SEGMENT = /^[A-Za-z0-9@._~-]+$/;

async function forward(request: NextRequest, ctx: RouteContext<"/api/campaigns/[[...path]]">): Promise<Response> {
  const { path = [] } = await ctx.params;
  if (path.some((segment) => !SEGMENT.test(segment) || segment === "." || segment === "..")) return detail("Invalid campaign path", 400);

  const headers = backendHeaders();
  for (const name of ["accept", "content-type", "last-event-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/campaigns${path.length ? `/${path.map(encodeURIComponent).join("/")}` : ""}${request.nextUrl.search}`), {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return detail("The Greenlight backend is not reachable. Is it running on BACKEND_URL?", 502);
  }
  const contentType = upstream.headers.get("Content-Type") ?? "application/json";
  const responseHeaders: Record<string, string> = { "Content-Type": contentType };
  if (contentType.startsWith("text/event-stream")) {
    Object.assign(responseHeaders, { "Cache-Control": "no-cache, no-transform", Connection: "keep-alive", "X-Accel-Buffering": "no" });
  }
  // Streamed straight through, never buffered.
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
