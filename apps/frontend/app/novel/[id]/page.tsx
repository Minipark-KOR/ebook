"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { fetchNovel, fetchChapters, fetchMetadata, Novel, Chapter, NovelMetadata } from "@/lib/api";

const PAGE_SIZE = 100;

export default function NovelPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const novelId = params.id as string;
  const initialPage = Math.max(1, Number(searchParams.get("page")) || 1);
  const initialFocus = Number(searchParams.get("focus")) || null;

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [metadata, setMetadata] = useState<NovelMetadata | null>(null);
  const [page, setPage] = useState(initialPage);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [metadataLoading, setMetadataLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [jumpInput, setJumpInput] = useState("");
  const [jumpError, setJumpError] = useState<string | null>(null);
  const [pendingJumpChapter, setPendingJumpChapter] = useState<number | null>(initialFocus);
  const chapterListRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!novelId) return;

    Promise.all([fetchNovel(novelId), fetchChapters(novelId, page)])
      .then(([novelData, chaptersData]) => {
        setNovel(novelData);
        setChapters(chaptersData.data);
        setTotalPages(Math.ceil(chaptersData.pagination.total / PAGE_SIZE));
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
      .finally(() => setMetadataLoading(false));
  }, [novel]);

  // After a pending jump resolves to a chapter rendered in the current page,
  // scroll it into view and clear the pending marker.
  useEffect(() => {
    if (pendingJumpChapter == null) return;
    const el = document.getElementById(`chapter-${pendingJumpChapter}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setPendingJumpChapter(null);
  }, [pendingJumpChapter, chapters]);

  // Keep URL query in sync with the current page (so back/forward and links work).
  // Also strip the transient `focus` query once the scroll target has been resolved.
  useEffect(() => {
    const currentPage = Number(searchParams.get("page")) || 1;
    const hasFocus = searchParams.has("focus");
    if (currentPage === page && !hasFocus) return;
    const qs = new URLSearchParams(searchParams.toString());
    if (page === 1) qs.delete("page");
    else qs.set("page", String(page));
    qs.delete("focus");
    const next = qs.toString() ? `?${qs.toString()}` : "";
    router.replace(`/novel/${novelId}${next}`, { scroll: false });
  }, [page, novelId, router, searchParams]);

  function handleJump(e: React.FormEvent) {
    e.preventDefault();
    setJumpError(null);
    const n = Number(jumpInput);
    if (!Number.isInteger(n) || n < 1) {
      setJumpError("회차 번호는 1 이상의 정수여야 합니다");
      return;
    }
    if (novel && n > novel.totalChapters) {
      setJumpError(`총 ${novel.totalChapters}화까지만 있습니다`);
      return;
    }
    const targetPage = Math.ceil(n / PAGE_SIZE);
    setJumpInput("");
    if (targetPage !== page) {
      setPendingJumpChapter(n);
      setPage(targetPage);
    } else {
      setPendingJumpChapter(n);
    }
  }

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
          <div className="flex flex-col md:flex-row gap-6">
            {/* 표지 이미지 */}
            {novel.coverUrl && (
              <div className="flex-shrink-0">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={novel.coverUrl}
                  alt={novel.title}
                  className="w-48 h-auto rounded-lg shadow-md object-cover"
                />
              </div>
            )}
            <div className="flex-1">
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
                {novel.title}
              </h1>
              <p className="text-lg text-gray-700 dark:text-gray-300 mb-2">
                {novel.author}
              </p>
              <div className="flex flex-wrap gap-2 mb-4">
                {novel.genre?.map((g, i) => (
                  <span
                    key={i}
                    className="px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 text-xs rounded"
                  >
                    {g}
                  </span>
                ))}
                {novel.status && novel.status !== "unknown" && (
                  <span className="px-2 py-1 bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 text-xs rounded">
                    {novel.status}
                  </span>
                )}
              </div>
              {/* 표지 아래로 EPUB 다운로드 버튼 - 우측 정렬 */}
              <div className="flex justify-end">
                <a
                  href={`/api/novels/${encodeURIComponent(novelId)}/epub`}
                  download
                  className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
                >
                  EPUB 다운로드
                </a>
              </div>
            </div>
          </div>
          
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
          {(metadata?.description || novel.description) && (
            <div className="bg-white dark:bg-gray-800 p-4 rounded-lg shadow mb-6">
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">
                줄거리
                {novel.namuUrl && (
                  <a
                    href={novel.namuUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 text-xs text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    (출처: namu.wiki ↗)
                  </a>
                )}
              </p>
              <p className="text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
                {novel.description || metadata?.description}
              </p>
            </div>
          )}

          {/* Local Info */}
          <div className="flex flex-wrap gap-4 text-sm text-gray-600 dark:text-gray-400 mb-6">
            <span>총 {novel.totalChapters}화</span>
            {novel.publisher && <span>출판사: {novel.publisher}</span>}
          </div>
        </div>

        {/* Chapter List */}
        <div className="sticky top-0 z-10 bg-gray-50 dark:bg-gray-900 -mx-4 px-4 py-2 mb-2">
          <form
            onSubmit={handleJump}
            className="flex flex-wrap items-center justify-end gap-2"
          >
            <label htmlFor="jump-input" className="text-sm text-gray-600 dark:text-gray-400">
              회차 바로가기
            </label>
            <input
              id="jump-input"
              type="number"
              min={1}
              max={novel.totalChapters}
              value={jumpInput}
              onChange={(e) => setJumpInput(e.target.value)}
              placeholder="회차 번호"
              className="w-24 px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
            />
            <button
              type="submit"
              className="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              이동
            </button>
            {jumpError && (
              <span className="text-sm text-red-500 basis-full text-right">{jumpError}</span>
            )}
          </form>
        </div>

        <div className="space-y-2" ref={chapterListRef}>
          {chapters.map((chapter) => (
            <Link
              key={chapter.wr_id}
              id={`chapter-${chapter.chapter}`}
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
          <div className="flex justify-center gap-2 mt-2">
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
