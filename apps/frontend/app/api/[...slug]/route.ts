import { NextRequest, NextResponse } from 'next/server';

const BACKEND = process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io';

async function proxy(req: NextRequest, slug: string[]) {
  const path = slug.join('/');
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io'}/api/${path}${new URL(req.url).search}`;
  
  // Origin 헤더 전달 (백엔드 CORS 응답 위해 필수)
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  const origin = req.headers.get('origin');
  if (origin) headers['Origin'] = origin;
  const referer = req.headers.get('referer');
  if (referer) headers['Referer'] = referer;
  
  const res = await fetch(url, {
    method: req.method,
    headers,
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : await req.text(),
  });
  
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
