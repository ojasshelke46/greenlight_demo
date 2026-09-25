// Server side only. Imported by route handlers under app/api; never by client components,
// so GREENLIGHT_API_KEY never reaches the browser.

const RUN_ID = /^[A-Za-z0-9_-]{1,64}$/;

function config(): { url: string; key: string } {
  const url = process.env.BACKEND_URL;
  const key = process.env.GREENLIGHT_API_KEY;
  if (!url || !key) {
    throw new Error("BACKEND_URL and GREENLIGHT_API_KEY must be set in .env.local");
  }
  return { url: url.replace(/\/+$/, ""), key };
}

export function backendHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  headers.set("Authorization", `Bearer ${config().key}`);
  return headers;
}

export function backendUrl(path: string): string {
  return `${config().url}${path}`;
}

export function validRunId(value: string | null): value is string {
  return value !== null && RUN_ID.test(value);
}

export function detail(message: string, status: number): Response {
  return Response.json({ detail: message }, { status });
}

/** Forward a JSON request to the backend and relay its status and body unchanged. */
export async function proxyJson(path: string, init?: RequestInit): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(backendUrl(path), {
      ...init,
      headers: backendHeaders({ Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) }),
      cache: "no-store",
    });
  } catch {
    return detail("The Greenlight backend is not reachable. Is it running on BACKEND_URL?", 502);
  }
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}
