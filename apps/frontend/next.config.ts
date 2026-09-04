import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 불변 정적 자산 비활성화 (JS 청크 404 해결)
  supportsImmutableAssets: false,
};

export default nextConfig;
