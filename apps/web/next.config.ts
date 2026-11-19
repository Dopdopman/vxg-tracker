import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Enable standalone output for Linux Docker builds while avoiding Windows symlink EPERM locally
  output: process.env.NEXT_OUTPUT_STANDALONE === "true" ? "standalone" : undefined,
};

export default nextConfig;

