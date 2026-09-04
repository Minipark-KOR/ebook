import { NextRequest, NextResponse } from 'next/server';

const BACKEND = process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io';

async function proxy(req: NextRequest, slug: string[]) {
  const path = slug.join('/');
  const url = `${process.env.NEXT_PUBLIC_API_URL || 'https://devforge.152-69-229-246.nip.io'}/api/${path}${new URL(req.url).search}`;
  
  const res = await fetch(url, {
    method: req.method,
    headers: {
      'Content-Type': 'application/json',
    },
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : await req.text(),
  });
  
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
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
