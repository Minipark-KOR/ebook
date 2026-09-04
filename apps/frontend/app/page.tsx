"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchNovels, Novel } from "@/lib/api";

export default function Home() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    console.log('[Home] fetchNovels start');
    fetchNovels()
      .then((data) => {
        console.log('[Home] fetchNovels success', data);
        setNovels(data);
      })
      .catch((err) => {
        console.error('[Home] fetchNovels error', err);
        setError(err.message);
      })
      .finally(() => {
        console.log('[Home] fetchNovels finally');
        setLoading(false);
      });
  }, []);

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
      <div className="max-w-4xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-8">
          전자책 라이브러리
        </h1>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {novels.map((novel) => (
            <Link
              key={novel.id}
              href={`/novel/${novel.id}`}
              prefetch={false}
              className="block p-6 bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-lg transition-shadow"
            >
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
                {novel.title}
              </h2>
              <p className="text-gray-600 dark:text-gray-400 mb-2">
                {novel.author}
              </p>
              <p className="text-sm text-gray-500 dark:text-gray-500">
                {novel.totalChapters}화
              </p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
