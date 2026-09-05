// 표지 이미지 프록시
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED = ["i.namu.wiki", "namu.wiki"];

export async function GET(req: NextRequest) {
  const url = req.nextUrl.searchParams.get("url");
  if (!url) return new NextResponse("Missing url", { status: 400 });

  let parsed: URL;
  try { parsed = new URL(url); }
  catch { return new NextResponse("Invalid url", { status: 400 }); }

  if (!ALLOWED.includes(parsed.hostname)) {
    return new NextResponse(`Domain not allowed: ${parsed.hostname}`, { status: 403 });
  }

  try {
    const resp = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://namu.wiki/",
      },
    });
    if (!resp.ok) {
      return new NextResponse(`Upstream ${resp.status}`, { status: 502 });
    }
    const body = await resp.arrayBuffer();
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": resp.headers.get("content-type") || "image/webp",
        "Cache-Control": "public, max-age=86400, immutable",
      },
    });
  } catch (e) {
    return new NextResponse(`Fetch error: ${e}`, { status: 502 });
  }
}
