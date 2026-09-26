import type { NextRequest } from "next/server";
import { backendHeaders, backendUrl, detail } from "@/lib/backend";

// The only proxy features use: /api/features/<path> -> {BACKEND_URL}/features/<path>, with the API key
// added here, server side. Features never add their own proxy routes.

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SEGMENT = /^[A-Za-z0-9._~-]+$/;
const FORWARDED_HEADERS = ["accept", "content-type", "last-event-id"];

async function forward(request: NextRequest, ctx: RouteContext<"/api/features/[...path]">): Promise<Response> {
  const { path } = await ctx.params;
  if (path.length === 0 || path.some((segment) => !SEGMENT.test(segment) || segment === "." || segment === "..")) {
    return detail("Invalid feature path", 400);
  }

  const headers = backendHeaders();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/features/${path.join("/")}${request.nextUrl.search}`), {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      // Closing an SSE connection in the browser closes the backend one too.
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
  // Pass the body straight through, never buffered, so each SSE event reaches the browser as it arrives.
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
