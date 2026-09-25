import type { NextRequest } from "next/server";
import { detail, proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const repo = request.nextUrl.searchParams.get("repo");
  if (!repo) return detail("Missing repo", 400);
  return proxyJson(`/access?repo=${encodeURIComponent(repo)}`);
}
