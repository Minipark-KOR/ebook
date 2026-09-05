"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { fetchNovels, Novel } from "@/lib/api";

export default function Home() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    fetchNovels()
      .then(setNovels)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (!mounted) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-gray-500 dark:text-gray-400">하이드레이션 대기 중...</div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-gray-500 dark:text-gray-400">로딩 중...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-red-500">에러: {error}</div>
      </div>
    );
  }

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
                  <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-500 text-sm">
                    표지 없음
                  </div>
                )}
                {novel.status && novel.status !== "unknown" && (
                  <span className="absolute top-2 right-2 px-2 py-1 bg-blue-600 text-white text-xs rounded">
                    {novel.status}
                  </span>
                )}
              </div>
              <div className="p-4">
                <h2 className="text-base font-semibold text-gray-900 dark:text-white mb-1 line-clamp-2">
                  {novel.title}
                </h2>
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