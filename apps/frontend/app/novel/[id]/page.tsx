import { Novel, Chapter } from "@/lib/api";
import NovelClient from "./NovelClient";

export const revalidate = 300;
export const dynamicParams = true;

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io";

async function fetchNovel(id: string): Promise<Novel | null> {
  try {
    const res = await fetch(`${API_BASE}/api/novels/${id}`, {
      next: { tags: ["novels"] },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function fetchAllChapters(id: string): Promise<Chapter[]> {
  try {
    const res = await fetch(`${API_BASE}/api/novels/${id}/chapters?page=1&limit=1000`, {
      next: { tags: ["novels"] },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.data || [];
  } catch {
    return [];
  }
}

export default async function NovelPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [novel, chapters] = await Promise.all([fetchNovel(id), fetchAllChapters(id)]);

  return <NovelClient novel={novel} chapters={chapters} novelId={id} />;
}