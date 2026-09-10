"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { Novel, MediaType } from "@/lib/api";

const MEDIA_TYPE_LABEL: Record<string, string> = {
  novel: "소설",
  comic: "만화",
  webtoon: "웹툰",
};

const TABS: { value: MediaType | "all"; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "novel", label: "소설" },
  { value: "comic", label: "만화" },
  { value: "webtoon", label: "웹툰" },
];

export default function LibraryClient({ novels }: { novels: Novel[] }) {
  const searchParams = useSearchParams();
  const router = useRouter();

  const requestedType = searchParams.get("type") as MediaType | null;
  const [type, setType] = useState<MediaType | "all">(
    requestedType && ["novel", "comic", "webtoon"].includes(requestedType)
      ? requestedType
      : "all"
  );

  const filtered = useMemo(
    () =>
      type === "all" ? novels : novels.filter((n) => n.mediaType === type),
    [novels, type]
  );

  function selectTab(t: MediaType | "all") {
    setType(t);
    const qs = new URLSearchParams(searchParams.toString());
    if (t === "all") qs.delete("type");
    else qs.set("type", t);
    const next = qs.toString() ? `?${qs.toString()}` : "";
    router.replace(`/${next}`, { scroll: false });
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
          전자책 라이브러리
        </h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          {filtered.length}권의 책
        </p>

        <div className="flex gap-2 mb-8">
          {TABS.map((t) => (
            <button
              key={t.value}
              onClick={() => selectTab(t.value)}
              className={
                "px-4 py-2 rounded text-sm font-medium transition-colors " +
                (type === t.value
                  ? "bg-blue-600 text-white"
                  : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700")
              }
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="grid gap-6 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
          {filtered.map((novel) => (
            <Link
              key={novel.id}
              href={`/novel/${novel.id}`}
              prefetch={false}
              className="group relative block bg-white dark:bg-gray-800 rounded-lg shadow hover:shadow-lg transition-shadow overflow-hidden"
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
                  <div className="flex items-center justify-center h-full p-4 text-gray-400 dark:text-gray-500 text-sm text-center">
                    {novel.title}
                  </div>
                )}
                {novel.mediaType && (
                  <span className="absolute top-2 left-2 px-2 py-1 text-white text-xs rounded bg-black/60">
                    {MEDIA_TYPE_LABEL[novel.mediaType] ?? novel.mediaType}
                  </span>
                )}
                {novel.status && (
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
                    {novel.status === "연재중" ? "연재 중" : novel.status}
                  </span>
                )}
              </div>

              <div className="absolute inset-0 bg-black/70 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-end p-4 pointer-events-none">
                <h3 className="text-white text-sm font-semibold mb-1 line-clamp-2">
                  {novel.title}
                </h3>
                <p className="text-gray-300 text-xs mb-1">{novel.author}</p>
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