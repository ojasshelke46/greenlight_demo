import { proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

// Backend health includes whether it can reach TrueForge.
export async function GET() {
  return proxyJson("/health");
}
