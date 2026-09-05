import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 불변 정적 자산 비활성화 (JS 청크 404 해결)
  supportsImmutableAssets: false,

  // Next.js 자체 라우트(/api/revalidate 등)가 [...slug] catch-all보다 우선이지만,
  // 명시적 rewrites로 보장.
  // /api/revalidate → 자체 라우트 (Vercel 측 on-demand ISR)
  rewrites: async () => {
    return [
      // revalidate는 자체 처리
      { source: "/api/revalidate", destination: "/api/revalidate" },
    ];
  },
};

export default nextConfig;
