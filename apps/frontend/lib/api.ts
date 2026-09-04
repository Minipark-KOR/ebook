// Same-origin proxy via Next.js app router.
// The catch-all route [/api/[...slug]] automatically URL-decodes path
// segments, so the client must send raw (un-encoded) Korean IDs.

export interface Novel {
  id: string;
  title: string;
  author: string;
  totalChapters: number;
  coverUrl: string | null;
}

export interface Chapter {
  wr_id: number;
  chapter: number;
  title: string;
  contentLength: number;
}

export interface ChapterDetail {
  wr_id: number;
  chapter: number;
  title: string;
  content: string;
  images: string[];
  prevChapter: number | null;
  nextChapter: number | null;
}

export interface PaginatedResponse<T> {
  data: T[];
  pagination: {
    page: number;
    limit: number;
    total: number;
  };
}

export interface NovelMetadata {
  title: string;
  authors: string[];
  publisher: string | null;
  year: string | null;
  isbn13: string | null;
  isbn10: string | null;
  language: string | null;
  coverUrl: string | null;
  description: string | null;
  subjects: string[];
  pageCount: number | null;
  source: string;
}

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export async function fetchNovels(): Promise<Novel[]> {
  const data = await fetchJson<{ novels: Novel[] }>("/api/novels");
  return data.novels;
}

export async function fetchNovel(id: string): Promise<Novel> {
  return fetchJson(`/api/novels/${id}`);
}

export async function fetchChapters(
  novelId: string,
  page: number = 1,
  limit: number = 100
): Promise<PaginatedResponse<Chapter>> {
  return fetchJson(`/api/novels/${novelId}/chapters?page=${page}&limit=${limit}`);
}

export async function fetchChapter(wrId: number): Promise<ChapterDetail> {
  return fetchJson(`/api/chapters/${wrId}`);
}

export async function fetchMetadata(
  title: string,
  service: "goob" | "openl" | "brave" = "brave"
): Promise<NovelMetadata | null> {
  try {
    return await fetchJson(`/api/metadata/lookup?title=${encodeURIComponent(title)}&service=${service}`);
  } catch (e) {
    return null;
  }
}

export async function searchMetadata(
  title: string,
  service: "goob" | "openl" | "brave" = "brave",
  maxResults: number = 5
): Promise<NovelMetadata[]> {
  return fetchJson(`/api/metadata/search?title=${encodeURIComponent(title)}&service=${service}&max_results=${maxResults}`);
}