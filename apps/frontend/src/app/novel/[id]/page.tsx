"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchNovel, fetchChapters, fetchMetadata, Novel, Chapter, NovelMetadata } from "@/lib/api";

export default function NovelPage() {
  const params = useParams();
  const novelId = params.id as string;

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [metadata, setMetadata] = useState<NovelMetadata | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [metadataLoading, setMetadataLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!novelId) return;

    Promise.all([fetchNovel(novelId), fetchChapters(novelId, page)])
      .then(([novelData, chaptersData]) => {
        setNovel(novelData);
        setChapters(chaptersData.data);
        setTotalPages(Math.ceil(chaptersData.pagination.total / 20));
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [novelId, page]);

  // Fetch metadata separately (using Brave for Korean web novels)
  useEffect(() => {
    if (!novel) return;
    setMetadataLoading(true);
    fetchMetadata(novel.title, "brave")
      .then(setMetadata)
      .catch(() => setMetadata(null)) // Silently fail - metadata is optional
      .finally(() => setMetadataLoading(false));
  }, [novel]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-gray-500 dark:text-gray-400">로딩 중...</div>
      </div>
    );
  }

  if (error || !novel) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-red-500">에러: {error || "소설을 찾을 수 없습니다"}</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-4xl mx-auto px-4 py-8">
        <Link
          href="/"
          className="text-blue-600 dark:text-blue-400 hover:underline mb-4 inline-block"
        >
          ← 라이브러리로 돌아가기
        </Link>

        {/* Novel Header with Metadata */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
            {novel.title}
          </h1>
          
          {/* Metadata Info Cards */}
          {metadata && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              {metadata.publisher && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">출판사</p>
                  <p className="font-medium text-gray-900 dark:text-white">{metadata.publisher}</p>
                </div>
              )}
              {metadata.year && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">출간 연도</p>
                  <p className="font-medium text-gray-900 dark:text-white">{metadata.year}년</p>
                </div>
              )}
              {metadata.isbn13 && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">ISBN-13</p>
                  <p className="font-mono text-sm text-gray-900 dark:text-white">{metadata.isbn13}</p>
                </div>
              )}
              {metadata.isbn10 && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">ISBN-10</p>
                  <p className="font-mono text-sm text-gray-900 dark:text-white">{metadata.isbn10}</p>
                </div>
              )}
              {metadata.authors.length > 0 && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">작가</p>
                  <p className="font-medium text-gray-900 dark:text-white">{metadata.authors.join(", ")}</p>
                </div>
              )}
              {metadata.pageCount && (
                <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow">
                  <p className="text-sm text-gray-500 dark:text-gray-400">페이지 수</p>
                  <p className="font-medium text-gray-900 dark:text-white">{metadata.pageCount.toLocaleString()}쪽</p>
                </div>
              )}
            </div>
          )}

          {/* Description */}
          {metadata?.description && (
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow mb-6">
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">줄거리</p>
              <p className="text-gray-700 dark:text-gray-300 whitespace-pre-wrap line-clamp-5">
                {metadata.description}
              </p>
            </div>
          )}

          {/* Local Info */}
          <div className="flex flex-wrap gap-4 text-sm text-gray-600 dark:text-gray-400 mb-6">
            <span>총 {novel.totalChapters}화</span>
            {metadata && <span>데이터 소스: {metadata.source}</span>}
          </div>
        </div>

        {/* Chapter List */}
        <div className="space-y-2">
          {chapters.map((chapter) => (
            <Link
              key={chapter.wr_id}
              href={`/novel/${novelId}/chapter/${chapter.wr_id}`}
              className="block p-4 bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-md transition-shadow"
            >
              <div className="flex justify-between items-center">
                <span className="text-gray-900 dark:text-white font-medium">
                  {chapter.title}
                </span>
                <span className="text-sm text-gray-500 dark:text-gray-500">
                  {chapter.contentLength?.toLocaleString()}자
                </span>
              </div>
            </Link>
          ))}
        </div>

        {totalPages > 1 && (
          <div className="flex justify-center gap-2 mt-8">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded disabled:opacity-50"
            >
              이전
            </button>
            <span className="px-4 py-2 text-gray-700 dark:text-gray-300">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-4 py-2 bg-gray-200 dark:bg-gray-700 rounded disabled:opacity-50"
            >
              다음
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
