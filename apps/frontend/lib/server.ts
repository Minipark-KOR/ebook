// Server-side helpers for the Vercel viewer (portal).
// devforge 백엔드 API 직접 호출: Vercel(서버) → nip.io → Caddy → FastAPI.

export const API_BASE = `${
  process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io"
}/api`;

export const DEVFORGE_BASE =
  process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io";

/** GET JSON with optional ISR revalidate (seconds). Returns null on any error. */
export async function getJSON<T>(path: string, revalidate = 300): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      next: { revalidate },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** GET JSON with cache disabled (fresh each request). */
export async function getJSONFresh<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function fmtKST(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export interface PortalSummary {
  status: string;
  time: string;
  open_incidents: number;
  last_backup: { name: string; size: number; time: string } | null;
}

export interface Incident {
  id: number;
  status: string;
  component: string;
  symptom?: string;
  action?: string;
  action_result?: string;
  fail_count?: number;
  reopen_count?: number;
  detected_at?: string;
  resolved_at?: string;
}

export interface BackupItem {
  name: string;
  size: number;
  time: string;
}

export interface NewsItem {
  id: number;
  title: string;
  title_ko?: string;
  source?: string;
  category?: string;
  summary_ko?: string;
}
