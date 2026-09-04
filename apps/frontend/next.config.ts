import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";
const backendUrl = process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: isProd ? `${backendUrl}/api/:path*` : "http://localhost:8089/api/:path*",
      },
    ];
  },
};

export default nextConfig;
