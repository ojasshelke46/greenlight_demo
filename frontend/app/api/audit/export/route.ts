import type { NextRequest } from "next/server";
import { backendHeaders, backendUrl, detail, validRunId } from "@/lib/backend";

export const dynamic = "force-dynamic";

const FORMATS = new Set(["md", "json"]);

// Streams the export straight through, keeping the backend's Content-Type and Content-Disposition,
// so the browser downloads greenlight_<run_id>.md or .json.
export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("runId");
  const format = request.nextUrl.searchParams.get("format") ?? "json";
  if (!validRunId(runId)) return detail("Missing or invalid runId", 400);
  if (!FORMATS.has(format)) return detail("format must be md or json", 400);

  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(`/runs/${runId}/audit/export?format=${format}`), {
      headers: backendHeaders(),
      cache: "no-store",
    });
  } catch {
    return detail("The Greenlight backend is not reachable. Is it running on BACKEND_URL?", 502);
  }

  const headers = new Headers();
  for (const name of ["Content-Type", "Content-Disposition"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
