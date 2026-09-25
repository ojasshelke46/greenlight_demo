import { proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET() {
  return proxyJson("/runs?limit=100");
}
