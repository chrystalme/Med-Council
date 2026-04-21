import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Pin Turbopack's workspace root to this monorepo so Next.js
  // doesn't mistakenly latch onto a stray lockfile further up the tree.
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
};

export default nextConfig;
