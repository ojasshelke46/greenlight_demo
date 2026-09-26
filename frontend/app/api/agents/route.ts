import { proxyJson } from "@/lib/backend";

export const dynamic = "force-dynamic";

// Every TrueForge agent Greenlight drives: role, name, real model, found, and whether the chat may start it.
export async function GET() {
  return proxyJson("/agents");
}
