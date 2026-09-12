import { NextRequest, NextResponse } from 'next/server';

const BACKEND = process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io';

// 자체 라우트가 있는 경로는 프록시하지 않음
const SELF_ROUTES = new Set(['revalidate']);

async function proxyToDevforge(req: NextRequest, slug: string[]): Promise<NextResponse> {
  const path = slug.join('/');
  const url = `${BACKEND}/api/${path}${new URL(req.url).search}`;

  const headers: Record<string, string> = {};
  const origin = req.headers.get('origin');
  if (origin) headers['Origin'] = origin;
  const referer = req.headers.get('referer');
  if (referer) headers['Referer'] = referer;
  // JSON 본문 파싱을 위해 Content-Type 전달 (없으면 백엔드가 body를 문자열로 받아 422)
  const contentType = req.headers.get('content-type');
  if (contentType) headers['Content-Type'] = contentType;

  try {
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
  } catch (e) {
    return NextResponse.json({ detail: `Backend proxy failed: ${e}` }, { status: 502 });
  }
}

// Node.js runtime - 외부 fetch(표지) + devforge 프록시 모두 지원
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type Props = { params: Promise<{ slug: string[] }> };

async function proxy(req: NextRequest, slug: string[]) {
  // 자체 라우트면 프록시 안 함
  if (slug.length === 1 && SELF_ROUTES.has(slug[0])) {
    return new NextResponse(
      JSON.stringify({ detail: 'Handled by internal route' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } }
    );
  }

  // 그 외 모든 경로는 devforge 백엔드로 프록시
  return proxyToDevforge(req, slug);
}

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