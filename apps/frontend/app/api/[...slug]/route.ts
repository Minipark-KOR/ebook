import { NextRequest, NextResponse } from 'next/server';
import { Pool } from '@neondatabase/serverless';

const BACKEND = process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io';

// 자체 라우트가 있는 경로는 프록시하지 않음
const SELF_ROUTES = new Set(['revalidate']);

// Neon DB (Edge runtime) - 빠른 직접 조회
const neonPool = process.env.NEON_DATABASE_URL
  ? new Pool({ connectionString: process.env.NEON_DATABASE_URL })
  : null;

async function neonQuery(sql: string, params: any[] = []): Promise<any[]> {
  if (!neonPool) return [];
  const client = await neonPool.connect();
  try {
    const { rows } = await client.query(sql, params);
    return rows;
  } finally {
    client.release();
  }
}

function rowToNovel(row: any) {
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

async function proxyToNeon(req: NextRequest, slug: string[]): Promise<NextResponse> {
  if (!neonPool) {
    return new NextResponse(
      JSON.stringify({ detail: 'Neon not configured' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }

  try {
    if (slug[0] === 'novels' && slug.length === 1) {
      // GET /api/novels
      const rows = await neonQuery(
        `SELECT id, title, author, total_chapters, cover_url, description, genre, status, publisher, namu_url
         FROM ebook_novels ORDER BY updated_at DESC`
      );
      return NextResponse.json(
        { novels: rows.map(rowToNovel) },
        { headers: { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=300' } }
      );
    }

    if (slug[0] === 'novels' && slug.length === 2) {
      // GET /api/novels/{id}
      const novelId = decodeURIComponent(slug[1]);
      const novelRows = await neonQuery(
        `SELECT id, title, author, total_chapters, cover_url, description, genre, status, publisher, namu_url
         FROM ebook_novels WHERE id = $1`,
        [novelId]
      );
      if (novelRows.length === 0) {
        return NextResponse.json({ detail: 'Not found' }, { status: 404 });
      }
      // 회차 목록도 함께
      const chapters = await neonQuery(
        `SELECT wr_id, chapter, title, content_length
         FROM ebook_chapters WHERE novel_id = $1
         ORDER BY wr_id LIMIT 1000`,
        [novelId]
      );
      const novel = rowToNovel(novelRows[0]);
      return NextResponse.json({
        ...novel,
        chapters: chapters.map((c: any) => ({
          wr_id: Number(c.wr_id),
          chapter: c.chapter,
          title: c.title,
          contentLength: c.content_length || 0,
        })),
      });
    }

    if (slug[0] === 'chapters' && slug.length === 2) {
      // GET /api/chapters/{wr_id}
      const wrId = parseInt(slug[1]);
      if (isNaN(wrId)) {
        return NextResponse.json({ detail: 'Invalid wr_id' }, { status: 400 });
      }
      const rows = await neonQuery(
        `SELECT wr_id, novel_id, chapter, title, content
         FROM ebook_chapters WHERE wr_id = $1`,
        [wrId]
      );
      if (rows.length === 0) {
        return NextResponse.json({ detail: 'Not found' }, { status: 404 });
      }
      const row = rows[0];
      // 이전/다음 회차
      const prevRows = await neonQuery(
        `SELECT wr_id FROM ebook_chapters
         WHERE novel_id = $1 AND wr_id < $2 ORDER BY wr_id DESC LIMIT 1`,
        [row.novel_id, wrId]
      );
      const nextRows = await neonQuery(
        `SELECT wr_id FROM ebook_chapters
         WHERE novel_id = $1 AND wr_id > $2 ORDER BY wr_id ASC LIMIT 1`,
        [row.novel_id, wrId]
      );
      return NextResponse.json({
        wr_id: Number(row.wr_id),
        chapter: row.chapter,
        title: row.title,
        content: row.content || "",
        images: [],
        prevChapter: prevRows.length > 0 ? Number(prevRows[0].wr_id) : null,
        nextChapter: nextRows.length > 0 ? Number(nextRows[0].wr_id) : null,
      });
    }
  } catch (e) {
    return NextResponse.json({ detail: String(e) }, { status: 500 });
  }

  return new NextResponse(
    JSON.stringify({ detail: 'Not implemented in Neon proxy' }),
    { status: 501 }
  );
}

async function proxy(req: NextRequest, slug: string[]) {
  // 자체 라우트면 프록시 안 함
  if (slug.length === 1 && SELF_ROUTES.has(slug[0])) {
    return new NextResponse(
      JSON.stringify({ detail: 'Handled by internal route' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } }
    );
  }

  // novels 관련 GET은 Neon DB 직접 조회 (self-hosted)
  if (
    req.method === 'GET' &&
    (slug[0] === 'novels' ||
      (slug[0] === 'chapters' && slug.length === 1) ||
      (slug[0] === 'novels' && slug.length === 2 && /^\d+$/.test(slug[1])))
  ) {
    return proxyToNeon(req, slug);
  }

  const path = slug.join('/');
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io'}/api/${path}${new URL(req.url).search}`;

  // Origin 헤더 전달 (백엔드 CORS 응답 위해 필수)
  const headers: Record<string, string> = {};
  const origin = req.headers.get('origin');
  if (origin) headers['Origin'] = origin;
  const referer = req.headers.get('referer');
  if (referer) headers['Referer'] = referer;

  const res = await fetch(url, {
    method: req.method,
    headers,
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : await req.text(),
  });

  const contentType = res.headers.get('content-type') || '';

  // JSON이 아닌 응답(이미지, EPUB 등 바이너리)은 그대로 스트리밍
  if (!contentType.includes('application/json')) {
    const blob = await res.blob();
    const responseHeaders: Record<string, string> = {};
    // Content-Type, Content-Disposition 등 핵심 헤더만 포워딩
    for (const [k, v] of res.headers.entries()) {
      // hop-by-hop 헤더 제외
      if (['connection', 'keep-alive', 'transfer-encoding'].includes(k.toLowerCase())) continue;
      responseHeaders[k] = v;
    }
    return new NextResponse(blob, {
      status: res.status,
      headers: responseHeaders,
    });
  }

  // JSON 응답은 그대로 포워딩
  const data = await res.json();
  const response = NextResponse.json(data, { status: res.status });

  // CORS 헤더 포워딩
  const corsHeaders = [
    'access-control-allow-origin',
    'access-control-allow-credentials',
    'access-control-allow-methods',
    'access-control-allow-headers',
    'access-control-expose-headers',
  ];
  for (const header of corsHeaders) {
    const value = res.headers.get(header);
    if (value) response.headers.set(header, value);
  }

  return response;
}

// Edge runtime - Neon DB 빠른 조회
export const runtime = 'edge';
export const dynamic = 'force-dynamic';

type Props = { params: Promise<{ slug: string[] }> };

export async function GET(req: NextRequest, { params }: Props) {
  const slug = (await params).slug;
  return proxy(req, slug);
}

export async function POST(req: NextRequest, { params }: Props) {
  const slug = (await params).slug;
  return proxy(req, slug);
}

export async function PUT(req: NextRequest, { params }: Props) {
  const slug = (await params).slug;
  return proxy(req, slug);
}

export async function DELETE(req: NextRequest, { params }: Props) {
  const slug = (await params).slug;
  return proxy(req, slug);
}
