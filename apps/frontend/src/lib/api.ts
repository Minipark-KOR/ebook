// Primary: OCI Caddy HTTPS, Fallback: Cloudflare Named Tunnel
const PRIMARY = (process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io").replace(/\/$/, "");
const FALLBACK = (process.env.NEXT_PUBLIC_API_FALLBACK_URL || "").replace(/\/$/, "");

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

async function fetchWithFallback<T>(path: string, timeoutMs = 8000): Promise<T> {
  const bases = [PRIMARY, FALLBACK].filter(Boolean) as string[];
  if (PRIMARY === "") bases.unshift("");
  let lastErr: unknown;
  for (const base of bases) {
    const url = `${base}${path}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} @ ${url}`);
      return (await res.json()) as T;
    } catch (e) {
      clearTimeout(timeoutId);
      lastErr = e;
      if (base === bases[bases.length - 1]) break;
      console.warn(`[api] primary failed, trying fallback: ${e}`);
    }
  }
  throw lastErr;
}

export async function fetchNovels(): Promise<Novel[]> {
  const data = await fetchWithFallback<{ novels: Novel[] }>("/api/novels");
  return data.novels;
}

function normalizeId(id: string): string {
  try {
    // 이미 인코딩된 것처럼 보이면(%%XX 패턴) 디코드 후 재인코드
    if (/%[0-9A-F]{2}/i.test(id)) {
      return decodeURIComponent(id);
    }
  } catch {
    // 디코드 실패 시 원본 사용
  }
  return id;
}

export async function fetchNovel(id: string): Promise<Novel> {
  const safeId = normalizeId(id);
  return fetchWithFallback(`/api/novels/${encodeURIComponent(safeId)}`);
}

export async function fetchChapters(
  novelId: string,
  page: number = 1,
  limit: number = 20
): Promise<PaginatedResponse<Chapter>> {
  return fetchWithFallback(`/api/novels/${encodeURIComponent(novelId)}/chapters?page=${page}&limit=${limit}`);
}

export async function fetchChapter(wrId: number): Promise<ChapterDetail> {
  return fetchWithFallback(`/api/chapters/${wrId}`);
}

export async function fetchMetadata(
  title: string,
  service: "goob" | "openl" | "brave" = "brave"
): Promise<NovelMetadata> {
  return fetchWithFallback(`/api/metadata/lookup?title=${encodeURIComponent(title)}&service=${service}`);
}

export async function searchMetadata(
  title: string,
  service: "goob" | "openl" | "brave" = "brave",
  maxResults: number = 5
): Promise<NovelMetadata[]> {
  return fetchWithFallback(`/api/metadata/search?title=${encodeURIComponent(title)}&service=${service}&max_results=${maxResults}`);
}
