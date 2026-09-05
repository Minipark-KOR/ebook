import Image from "next/image";
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
              className="group relative block bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-lg transition-shadow overflow-hidden"
            >
              {/* 표지 이미지 */}
              <div className="relative w-full aspect-[5/7] bg-gray-100 dark:bg-gray-700">
                {novel.coverUrl ? (
                  <Image
                    src={novel.coverUrl}
                    alt={novel.title}
                    fill
                    sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw"
                    className="object-cover"
                    unoptimized
                  />
                ) : (
                  <div className="flex items-center justify-center h-full p-4 text-gray-400 dark:text-gray-500 text-sm text-center">
                    {novel.title}
                  </div>
                )}
                {novel.status && novel.status !== "unknown" && (
                  <span
                    className={
                      "absolute top-2 right-2 px-2 py-1 text-white text-xs rounded " +
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

              {/* 호버 시 메타데이터 오버레이 */}
              <div className="absolute inset-0 bg-black/70 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-4 pointer-events-none">
                <h3 className="text-white text-sm font-semibold mb-1 line-clamp-2">
                  {novel.title}
                </h3>
                <p className="text-gray-300 text-xs mb-1">
                  {novel.author}
                </p>
                {novel.description && (
                  <p className="text-gray-400 text-xs leading-relaxed line-clamp-4">
                    {novel.description}
                  </p>
                )}
                <div className="flex flex-wrap gap-1 mt-1">
                  {novel.genre?.slice(0, 3).map((g, i) => (
                    <span
                      key={i}
                      className="px-1.5 py-0.5 bg-white/20 text-white text-[10px] rounded"
                    >
                      {g}
                    </span>
                  ))}
                  <span className="px-1.5 py-0.5 bg-white/20 text-white text-[10px] rounded">
                    {novel.totalChapters}화
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}