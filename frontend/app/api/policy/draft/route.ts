import type { NextRequest } from "next/server";
import { detail, proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

// Starts the policy agent drafting a .greenlight.yml. The backend refuses a repo that already has one.
export async function POST(request: NextRequest) {
  let body: { repo?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with repo", 400);
  }
  if (typeof body.repo !== "string" || body.repo.trim() === "") return detail("repo is required", 400);
  return proxyJson(`/features/policy/draft?repo=${encodeURIComponent(body.repo.trim())}`, { method: "POST", body: "{}" });
}
