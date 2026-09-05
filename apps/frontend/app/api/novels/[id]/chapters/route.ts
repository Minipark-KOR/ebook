// 회차 목록 (Neon 직접 조회)
import { NextRequest, NextResponse } from "next/server";
import { Pool } from "@neondatabase/serverless";

export const runtime = "edge";
export const dynamic = "force-dynamic";

const pool = process.env.NEON_DATABASE_URL
  ? new Pool({ connectionString: process.env.NEON_DATABASE_URL })
  : null;

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  if (!pool) {
    return NextResponse.json({ data: [], pagination: { page: 1, limit: 0, total: 0 } });
  }

  const { id } = await params;
  const novelId = decodeURIComponent(id);
  const page = parseInt(req.nextUrl.searchParams.get("page") || "1");
  const limit = Math.min(parseInt(req.nextUrl.searchParams.get("limit") || "20"), 100);
  const offset = (page - 1) * limit;

  const client = await pool.connect();
  try {
    const countResult = await client.query(
      "SELECT COUNT(*) as total FROM ebook_chapters WHERE novel_id = $1",
      [novelId]
    );
    const total = parseInt(countResult.rows[0]?.total || 0);

    const result = await client.query(
      `SELECT wr_id, chapter, title, content_length
       FROM ebook_chapters
       WHERE novel_id = $1
       ORDER BY wr_id
       LIMIT $2 OFFSET $3`,
      [novelId, limit, offset]
    );

    return NextResponse.json(
      {
        data: result.rows.map((r: any) => ({
          wr_id: Number(r.wr_id),
          chapter: r.chapter,
          title: r.title,
          contentLength: r.content_length || 0,
        })),
        pagination: { page, limit, total },
      },
      { headers: { "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300" } }
    );
  } finally {
    client.release();
  }
}
