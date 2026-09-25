import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root has its own package-lock.json (the demo library); keep Turbopack rooted here.
  turbopack: { root: path.join(__dirname) },
};

export default nextConfig;
