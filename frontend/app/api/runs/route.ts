import type { NextRequest } from "next/server";
import { detail, proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

// Top level runs only; helper agent runs are listed per run at /api/runs/<id>/children.
export async function GET() {
  return proxyJson("/runs?limit=100");
}

const CHAT_ROLES = new Set(["fixer", "scout", "policy"]);

// Starts a run for a chat startable agent. The backend validates the rest (repo access, target, policy file).
export async function POST(request: NextRequest) {
  let body: { role?: unknown; repo?: unknown; mode?: unknown; target?: unknown };
  try {
    body = await request.json();
  } catch {
    return detail("Body must be JSON with a role", 400);
  }
  const role = body.role ?? "fixer";
  if (typeof role !== "string" || !CHAT_ROLES.has(role)) return detail("role must be fixer, scout or policy", 400);
  const forward: Record<string, unknown> = { role };
  for (const key of ["repo", "mode", "target"] as const) {
    if (typeof body[key] === "string") forward[key] = body[key];
  }
  return proxyJson("/runs", { method: "POST", body: JSON.stringify(forward) });
}
