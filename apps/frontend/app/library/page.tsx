import { Suspense } from "react";
import { Novel } from "@/lib/api";
import LibraryClient from "../LibraryClient";

const API_BASE = `${
  process.env.NEXT_PUBLIC_API_URL || "https://devforge.152-69-229-246.nip.io"
}/api`;

export const revalidate = 300;

async function fetchNovelsServer(): Promise<Novel[]> {
  try {
    const res = await fetch(`${API_BASE}/novels`, {
      next: { revalidate: 300, tags: ["novels"] },
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.novels || [];
  } catch {
    return [];
  }
}

export default async function LibraryPage() {
  const novels = await fetchNovelsServer();
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-50 dark:bg-gray-900" />}>
      <LibraryClient novels={novels} />
    </Suspense>
  );
}
