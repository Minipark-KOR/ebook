// On-demand revalidation endpoint.
// devforge 백엔드에서 챕터 저장 후 호출.
// 보안: 환경변수 VERCEL_REVALIDATE_TOKEN으로 인증.

import { NextRequest, NextResponse } from "next/server";
import { revalidatePath, revalidateTag } from "next/cache";

const TOKEN = process.env.VERCEL_REVALIDATE_TOKEN || "dev-insecure-token";

export async function POST(req: NextRequest) {
  // 1) 인증
  const auth = req.headers.get("authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "");
  if (token !== TOKEN) {
    return NextResponse.json({ ok: false, error: "Unauthorized" }, { status: 401 });
  }

  // 2) path/tag 추출 (body or query)
  let body: { paths?: string[]; tags?: string[] } = {};
  try {
    body = await req.json();
  } catch {
    // query 사용
    const p = req.nextUrl.searchParams.get("path");
    if (p) body.paths = [p];
  }

  // 3) revalidate 실행
  const paths = body.paths || ["/"];
  const tags = body.tags || ["novels"];

  const results: Array<{ path: string; ok: boolean; error?: string }> = [];
  for (const p of paths) {
    try {
      revalidatePath(p);
      results.push({ path: p, ok: true });
    } catch (e) {
      results.push({ path: p, ok: false, error: String(e) });
    }
  }
  for (const t of tags) {
    try {
      revalidateTag(t);
    } catch {
      // ignore
    }
  }

  return NextResponse.json({ ok: true, results, timestamp: Date.now() });
}

export async function GET() {
  // health check
  return NextResponse.json({ ok: true, service: "revalidate" });
}