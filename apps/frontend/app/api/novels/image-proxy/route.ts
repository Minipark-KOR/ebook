// 표지 이미지 프록시 - devforge 백엔드 경유 (Vercel → namu.wiki 직접 fetch 실패)
// Vercel IP는 namu.wiki CDN에서 403 차단하므로 devforge를 통해 우회

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : "https://devforge.152-69-229-246.nip.io/api";

export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url");
  if (!url) {
    return new NextResponse("Missing url parameter", { status: 400 });
  }

  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return new NextResponse("Invalid url", { status: 400 });
  }

  const ALLOWED_DOMAINS = ["i.namu.wiki", "namu.wiki"];
  if (!ALLOWED_DOMAINS.includes(parsed.hostname)) {
    return new NextResponse(`Domain not allowed: ${parsed.hostname}`, { status: 403 });
  }

  // devforge 백엔드로 프록시 (Vercel → namu.wiki 직접 fetch는 403)
  try {
    const backendUrl = `${BACKEND}/novels/image-proxy?url=${encodeURIComponent(url)}`;
    const resp = await fetch(backendUrl, {
      headers: { "User-Agent": "Mozilla/5.0" },
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
    return new NextResponse(`Fetch error: ${e}`, { status: 502 });
  }
}