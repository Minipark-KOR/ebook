// GET /api/novels/{id}/epub - EPUB 다운로드 프록시
// devforge 백엔드 API로 EPUB 바이너리 직접 fetch (Edge runtime)

import { NextRequest, NextResponse } from "next/server";

export const runtime = "edge";
export const dynamic = "force-dynamic";

const BACKEND = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : "https://devforge.152-69-229-246.nip.io/api";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const novelId = decodeURIComponent(id);

  const backendUrl = `${BACKEND}/novels/${encodeURIComponent(novelId)}/epub`;

  try {
    const resp = await fetch(backendUrl, {
      headers: { "User-Agent": "Mozilla/5.0" },
    });

    if (!resp.ok) {
      return NextResponse.json(
        { detail: `EPUB fetch failed: ${resp.status}` },
        { status: resp.status }
      );
    }

    const blob = await resp.arrayBuffer();
    return new NextResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": "application/epub+zip",
        "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(novelId)}.epub`,
        "Content-Length": String(blob.byteLength),
      },
    });
  } catch (e) {
    return NextResponse.json(
      { detail: `EPUB proxy error: ${e}` },
      { status: 502 }
    );
  }
}
