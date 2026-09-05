import Link from "next/link";
import { Novel } from "@/lib/api";

// devforge 백엔드 API 직접 호출 (Vercel → nip.io → devforge)
const API_BASE = process.env.NEXT_PUBLIC_API_URL
  ? `${process.env.NEXT_PUBLIC_API_URL}/api`
  : "https://devforge.152-69-229-246.nip.io/api";

// 5분마다 자동 갱신 (ISR: Incremental Static Regeneration)
export const revalidate = 300;

async function fetchNovelsServer(): Promise<Novel[]> {
  const res = await fetch(`${API_BASE}/novels`, {
    next: { revalidate: 300, tags: ["novels"] },
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    console.error(`Failed to fetch novels: ${res.status}`);
    return [];
  }
  const data = await res.json();
  return data.novels || [];
}

export default async function Home() {
  const novels = await fetchNovelsServer();

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
          전자책 라이브러리
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-8">
          {novels.length}권의 책
        </p>
        <div className="grid gap-6 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          {novels.map((novel) => (
            <Link
              key={novel.id}
              href={`/novel/${novel.id}`}
              prefetch={false}
              className="block bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-lg transition-shadow overflow-hidden"
            >
              <div className="p-4">
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-base font-semibold text-gray-900 dark:text-white line-clamp-2">
                    {novel.title}
                  </h2>
                  {novel.status && novel.status !== "unknown" && (
                    <span
                      className={
                        "shrink-0 ml-2 px-2 py-0.5 text-white text-xs rounded " +
                        (novel.status === "완결"
                          ? "bg-gray-700"
                          : novel.status === "단편"
                          ? "bg-purple-600"
                          : "bg-blue-600")
                      }
                    >
                      {novel.status}
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                  {novel.author}
                </p>
                <div className="flex flex-wrap gap-1 mb-2">
                  {novel.genre?.slice(0, 3).map((g, i) => (
                    <span
                      key={i}
                      className="px-2 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs rounded"
                    >
                      {g}
                    </span>
                  ))}
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-500">
                  총 {novel.totalChapters}화
                </p>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}