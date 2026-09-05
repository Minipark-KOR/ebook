// Vercel 측: Neon DB에서 직접 조회
// devforge 프록시 우회 → 200-500ms로 빠름

import { NextRequest, NextResponse } from "next/server";
import { Pool } from "@neondatabase/serverless";

export const runtime = "edge";
export const dynamic = "force-dynamic";

const pool = new Pool({
  connectionString: process.env.NEON_DATABASE_URL,
});

async function query<T = any>(sql: string, params: any[] = []): Promise<T[]> {
  const client = await pool.connect();
  try {
    const { rows } = await client.query(sql, params);
    return rows as T[];
  } finally {
    client.release();
  }
}

interface NovelRow {
  id: string;
  title: string;
  author: string;
  total_chapters: number;
  cover_url: string | null;
  description: string | null;
  genre: string[] | null;
  status: string | null;
  publisher: string | null;
  namu_url: string | null;
}

interface ChapterRow {
  wr_id: string | number;
  chapter: number;
  title: string;
  content_length: number | null;
  novel_id: string;
  content: string | null;
}

function rowToNovel(row: NovelRow) {
  return {
    id: row.id,
    title: row.title,
    author: row.author || "미상",
    totalChapters: row.total_chapters || 0,
    coverUrl: row.cover_url,
    description: row.description || "",
    genre: row.genre || [],
    status: row.status || "unknown",
    publisher: row.publisher || "북토끼",
    namuUrl: row.namu_url,
  };
}

export async function GET() {
  try {
    const rows = await query<NovelRow>(
      `SELECT id, title, author, total_chapters, cover_url, description, genre, status, publisher, namu_url
       FROM ebook_novels ORDER BY updated_at DESC`
    );
    return NextResponse.json(
      { novels: rows.map(rowToNovel) },
      {
        headers: {
          "Cache-Control": "public, s-maxage=60, stale-while-revalidate=300",
        },
      }
    );
  } catch (e) {
    return NextResponse.json(
      { novels: [], error: String(e) },
      { status: 500 }
    );
  }
}