"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchChapter, ChapterDetail } from "@/lib/api";

export default function ChapterPage() {
  const params = useParams();
  const wrId = Number(params.wr_id);
  const novelId = params.id as string;

  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState(18);

  useEffect(() => {
    if (!wrId) return;

    fetchChapter(wrId)
      .then(setChapter)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [wrId]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-gray-500 dark:text-gray-400">로딩 중...</div>
      </div>
    );
  }

  if (error || !chapter) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-red-500">에러: {error || "회차를 찾을 수 없습니다"}</div>
      </div>
    );
  }

  const paragraphs = chapter.content
    .split("\n")
    .filter((line) => line.trim());

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-3xl mx-auto px-4 py-8">
        <Link
          href={`/novel/${novelId}`}
          className="text-blue-600 dark:text-blue-400 hover:underline mb-4 inline-block"
        >
          ← 회차 목록으로 돌아가기
        </Link>

        <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">
          {chapter.title}
        </h1>

        <div className="flex gap-2 mb-6">
          <button
            onClick={() => setFontSize((s) => Math.max(12, s - 2))}
            className="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded text-sm"
          >
            A-
          </button>
          <span className="px-3 py-1 text-gray-600 dark:text-gray-400 text-sm">
            {fontSize}px
          </span>
          <button
            onClick={() => setFontSize((s) => Math.min(28, s + 2))}
            className="px-3 py-1 bg-gray-200 dark:bg-gray-700 rounded text-sm"
          >
            A+
          </button>
        </div>

        <article
          className="prose prose-lg dark:prose-invert max-w-none"
          style={{ fontSize: `${fontSize}px`, lineHeight: "1.8" }}
        >
          {paragraphs.map((paragraph, idx) => (
            <p key={idx} className="mb-4 text-gray-800 dark:text-gray-200">
              {paragraph}
            </p>
          ))}
        </article>

        <div className="flex justify-between mt-8 pt-4 border-t border-gray-200 dark:border-gray-700">
          {chapter.prevChapter ? (
            <Link
              href={`/novel/${novelId}/chapter/${chapter.prevChapter}`}
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              ← 이전 화
            </Link>
          ) : (
            <span />
          )}
          {chapter.nextChapter ? (
            <Link
              href={`/novel/${novelId}/chapter/${chapter.nextChapter}`}
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              다음 화 →
            </Link>
          ) : (
            <span />
          )}
        </div>
      </div>
    </div>
  );
}
