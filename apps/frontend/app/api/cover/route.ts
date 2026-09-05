// 표지 이미지 프록시 (Node.js runtime - Vercel이 외부 도메인 fetch 차단하므로
// devforge 백엔드 API로 리다이렉트). namu.wiki i.namu.wiki는 FlareSolverr 우회 필요.

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io";

export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url");
  if (!url) {
    return new NextResponse("Missing url", { status: 400 });
  }

  // 백엔드(devforge)로 프록시 - devforge가 FlareSolverr로 namu.wiki fetch
  // 백엔드 API에 image-proxy가 있음 (FlareSolverr 우회)
  const backendUrl = `${BACKEND}/api/novels/image-proxy?url=${encodeURIComponent(url)}`;

  try {
    const resp = await fetch(backendUrl, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://namu.wiki/",
      },
    });

    if (!resp.ok) {
      return new NextResponse(`Upstream error: ${resp.status}`, { status: 502 });
    }

    const contentType = resp.headers.get("content-type") || "image/webp";
    const body = await resp.arrayBuffer();

    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "public, max-age=86400, immutable",
      },
    });
  } catch (e) {
    return new NextResponse(`Proxy error: ${e}`, { status: 502 });
  }
}
