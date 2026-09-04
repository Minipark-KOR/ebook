import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 로컬 dev: WSL/로컬에서 OCI 8089로 프록시. 프로덕션에서는 NEXT_PUBLIC_API_URL이 직접 사용됨.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8089/api/:path*",
      },
    ];
  },
};

export default nextConfig;
