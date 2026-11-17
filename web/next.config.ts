import type { NextConfig } from "next";

const config: NextConfig = {
  devIndicators: false,
  async rewrites() {
    const backend = process.env.AJORA_API_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: backend + "/api/:path*" }];
  },
};

export default config;
