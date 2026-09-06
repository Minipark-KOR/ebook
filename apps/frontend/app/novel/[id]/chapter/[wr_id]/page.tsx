"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { fetchChapter, ChapterDetail } from "@/lib/api";

const PAGE_SIZE = 20;

export default function ChapterPage() {
  const params = useParams();
  const router = useRouter();
  const wrId = Number(params.wr_id);
  const novelId = params.id as string;

  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listPage, setListPage] = useState<number | null>(null);
  const [fontSize, setFontSize] = useState(18);
  const [navOpen, setNavOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!wrId) return;

    fetchChapter(wrId)
      .then((data) => {
        setChapter(data);
        setListPage(Math.max(1, Math.ceil(data.chapter / PAGE_SIZE)));
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [wrId]);

  // Close overlay with ESC for desktop users.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  const goPrev = useCallback(() => {
    if (chapter?.prevChapter) {
      router.push(`/novel/${novelId}/chapter/${chapter.prevChapter}`);
    }
  }, [chapter, novelId, router]);

  const goNext = useCallback(() => {
    if (chapter?.nextChapter) {
      router.push(`/novel/${novelId}/chapter/${chapter.nextChapter}`);
    }
  }, [chapter, novelId, router]);

  function buildListHref(focusChapter: number): string {
    const params = new URLSearchParams();
    if (listPage && listPage > 1) params.set("page", String(listPage));
    params.set("focus", String(focusChapter));
    const qs = params.toString();
    return `/novel/${novelId}${qs ? `?${qs}` : ""}`;
  }

  // 화면 터치: 위 15% = 이전화, 아래 15% = 다음화, 가운데 = 레이어 열기
  const handleTap = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const h = rect.height;
      const ratio = y / h;

      if (ratio < 0.15) {
        // 위쪽 15%: 이전화 이동
        if (chapter?.prevChapter) goPrev();
      } else if (ratio > 0.85) {
        // 아래쪽 15%: 다음화 이동
        if (chapter?.nextChapter) goNext();
      } else {
        // 가운데: 레이어 열기
        setNavOpen(true);
      }
    },
    [chapter, goPrev, goNext]
  );

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
        <div onClick={(e) => e.stopPropagation()}>
          <Link
            href={buildListHref(chapter.chapter)}
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
          {chapter.images && chapter.images.length > 0 && (
            <div className="mt-8 space-y-4">
              {chapter.images.map((imgUrl: string, idx: number) => (
                <div key={idx} className="text-center">
                  <img
                    src={imgUrl}
                    alt={`삽화 ${idx + 1}`}
                    className="max-w-full h-auto rounded-lg shadow-lg mx-auto"
                    loading="lazy"
                  />
                </div>
              ))}
            </div>
          )}
        </article>

        <div onClick={(e) => e.stopPropagation()}>
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

      {/* 터치 네비게이션 레이어 — 화면 상하 15%만 (pointer-events-none으로 본문 클릭 통과) */}
      <nav
        className="fixed inset-0 z-0 flex flex-col pointer-events-none"
        onClick={handleTap}
      >
        {/* 위쪽 15%: 위로 스크롤 */}
        <div
          className="h-[15%] pointer-events-auto cursor-pointer"
          title="위로 스크롤"
        />

        {/* 가운데 70%: 회차목록 overlay */}
        <div className="flex-1 pointer-events-auto cursor-pointer" />

        {/* 아래쪽 15%: 아래로 스크롤 */}
        <div
          className="h-[15%] pointer-events-auto cursor-pointer"
          title="아래로 스크롤"
        />
      </nav>

      {navOpen && (
        <div
          className="fixed inset-0 z-50 flex"
          role="dialog"
          aria-label="네비게이션"
          onClick={() => setNavOpen(false)}
        >
          {/* Left zone: previous chapter */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (chapter.prevChapter) {
                window.location.href = `/novel/${novelId}/chapter/${chapter.prevChapter}`;
              } else {
                setNavOpen(false);
              }
            }}
            disabled={!chapter.prevChapter}
            className="flex-[0_0_25%] md:flex-[0_0_20%] h-full bg-black/30 hover:bg-black/40 disabled:bg-black/10 disabled:cursor-not-allowed transition-colors flex items-center justify-start pl-3 md:pl-6"
            aria-label="이전 회차"
          >
            {chapter.prevChapter && (
              <svg
                className="w-10 h-10 md:w-14 md:h-14 text-white drop-shadow-lg"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
              </svg>
            )}
          </button>

          {/* Center zone: open chapter list */}
          <div className="flex-1 h-full flex items-center justify-center pointer-events-none">
            <Link
              href={buildListHref(chapter.chapter)}
              onClick={(e) => e.stopPropagation()}
              className="pointer-events-auto px-6 py-3 bg-white/90 dark:bg-gray-800/90 hover:bg-white dark:hover:bg-gray-800 rounded-lg shadow-lg text-gray-900 dark:text-white font-medium backdrop-blur"
            >
              회차 목록
            </Link>
          </div>

          {/* Right zone: next chapter */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (chapter.nextChapter) {
                window.location.href = `/novel/${novelId}/chapter/${chapter.nextChapter}`;
              } else {
                setNavOpen(false);
              }
            }}
            disabled={!chapter.nextChapter}
            className="flex-[0_0_25%] md:flex-[0_0_20%] h-full bg-black/30 hover:bg-black/40 disabled:bg-black/10 disabled:cursor-not-allowed transition-colors flex items-center justify-end pr-3 md:pr-6"
            aria-label="다음 회차"
          >
            {chapter.nextChapter && (
              <svg
                className="w-10 h-10 md:w-14 md:h-14 text-white drop-shadow-lg"
                viewBox="0 0 24 24"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
              </svg>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
