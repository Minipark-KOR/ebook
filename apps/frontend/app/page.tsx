import { Suspense } from "react";
import { Novel } from "@/lib/api";
import LibraryClient from "./LibraryClient";

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
    <Suspense fallback={<div className="min-h-screen bg-gray-50 dark:bg-gray-900" />}>
      <LibraryClient novels={novels} />
    </Suspense>
  );
}