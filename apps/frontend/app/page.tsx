import Link from "next/link";
import { Novel } from "@/lib/api";
import {
  DEVFORGE_BASE,
  fmtKST,
  getJSON,
  getJSONFresh,
  NewsItem,
  PortalSummary,
} from "@/lib/server";

export const revalidate = 60;

export default async function PortalHome() {
  const [summary, novelsRes, dates] = await Promise.all([
    getJSONFresh<PortalSummary>("/portal/summary"),
    getJSON<{ novels: Novel[] }>("/novels", 300),
    getJSON<{ date: string }[]>("/news/dates", 300),
  ]);

  const latest = dates?.[0]?.date;
  const news = latest
    ? await getJSON<NewsItem[]>(`/news/articles?date=${latest}`, 300)
    : null;

  const novels = (novelsRes?.novels || []).slice(0, 3);
  const newsTop = (news || []).slice(0, 3);
  const ok = summary?.status === "ok";

  const card = "rounded-xl border border-gray-200 dark:border-gray-800 p-4 hover:shadow transition";
  const dim = "text-sm text-gray-600 dark:text-gray-300";

  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <header className="flex flex-wrap items-center justify-between gap-2 mb-6">
        <h1 className="text-2xl font-bold">DevForge</h1>
        <span className={`${dim} flex items-center gap-2`}>
          <span className={`inline-block w-2 h-2 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`} />
          {ok ? "정상" : "확인 필요"}
          <span className="text-gray-400">·</span>
          incident {summary?.open_incidents ?? "?"}
          <span className="text-gray-400">·</span>
          백업 {fmtKST(summary?.last_backup?.time)}
        </span>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <Link href="/library" className={card}>
          <h2 className="font-semibold mb-2">📚 도서관</h2>
          <ul className={`${dim} space-y-1`}>
            {novels.length ? (
              novels.map((n) => (
                <li key={n.id} className="truncate">{n.title}</li>
              ))
            ) : (
              <li className="text-gray-400">—</li>
            )}
          </ul>
        </Link>

        <Link href="/news" className={card}>
          <h2 className="font-semibold mb-2">📰 뉴스</h2>
          <ul className={`${dim} space-y-1`}>
            {newsTop.length ? (
              newsTop.map((a) => (
                <li key={a.id} className="truncate">{a.title_ko || a.title}</li>
              ))
            ) : (
              <li className="text-gray-400">—</li>
            )}
          </ul>
        </Link>

        <Link href="/status" className={card}>
          <h2 className="font-semibold mb-2">📡 상태</h2>
          <p className={dim}>미해결 incident: {summary?.open_incidents ?? "?"}</p>
          <p className={`${dim} truncate`}>최근 백업: {summary?.last_backup?.name ?? "—"}</p>
        </Link>
      </div>

      <div className={`mt-6 ${dim} flex flex-wrap gap-4`}>
        <a href={`${DEVFORGE_BASE}/send`} className="underline">🔗 파일교환(devforge)</a>
        <Link href="/library" className="underline">도서관</Link>
        <Link href="/news" className="underline">뉴스</Link>
        <Link href="/status" className="underline">상태</Link>
      </div>
    </main>
  );
}
