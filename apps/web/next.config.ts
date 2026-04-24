import type { NextConfig } from "next";
import path from "node:path";

// FastAPI origin. Local default :8000; in prod, set NEXT_PUBLIC_API_BASE_URL
// (and API_BASE_URL) to the deployed backend URL so rewrites proxy there.
const API_ORIGIN =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8000";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle so the Docker image can run Next.js
  // without the full node_modules tree. Produced under `.next/standalone/`.
  output: "standalone",
  // Pin Turbopack's workspace root to this monorepo so Next.js
  // doesn't mistakenly latch onto a stray lockfile further up the tree.
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  // Same-origin proxy so browser requests to /api/* reach the FastAPI backend
  // without CORS and without exposing a second host. Keeps `fetch('/api/me')`
  // (and direct browser navigation) working against the Next dev server.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
