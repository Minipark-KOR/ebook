import { ChapterDetail } from "@/lib/api";
import ChapterClient from "./ChapterClient";

export const revalidate = 300;
export const dynamicParams = true;

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io";

async function fetchChapter(wrId: number): Promise<ChapterDetail | null> {
  try {
    const res = await fetch(`${API_BASE}/api/chapters/${wrId}`, {
      next: { tags: ["chapters"] },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function ChapterPage({
  params,
}: {
  params: Promise<{ id: string; wr_id: string }>;
}) {
  const { id, wr_id } = await params;
  const chapter = await fetchChapter(Number(wr_id));

  return <ChapterClient novelId={id} chapter={chapter} />;
}