import Link from "next/link";
import { NewsItem } from "@/lib/server";

export default function NewsView({
  date,
  dates,
  articles,
}: {
  date?: string;
  dates: { date: string }[];
  articles: NewsItem[];
}) {
  const dim = "text-sm text-gray-600 dark:text-gray-300";
  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <h1 className="text-2xl font-bold mb-4">📰 뉴스</h1>

      <div className="flex flex-wrap gap-2 mb-6">
        {dates.slice(0, 12).map((d) => (
          <Link
            key={d.date}
            href={`/news/${d.date}`}
            className={`text-xs px-2 py-1 rounded border ${
              d.date === date
                ? "bg-blue-600 text-white border-blue-600"
                : "border-gray-200 dark:border-gray-700"
            }`}
          >
            {d.date}
          </Link>
        ))}
      </div>

      <h2 className={`${dim} mb-3`}>
        {date ?? "최신"} — {articles.length}건
      </h2>

      <ul className="divide-y divide-gray-100 dark:divide-gray-800">
        {articles.map((a) => (
          <li key={a.id} className="py-3">
            <div className="font-medium">{a.title_ko || a.title}</div>
            {a.title_ko && a.title !== a.title_ko ? (
              <div className="text-xs text-gray-500 mt-0.5">{a.title}</div>
            ) : null}
            <div className={`${dim} mt-1 flex gap-2`}>
              {a.source ? <span>{a.source}</span> : null}
              {a.category ? <span className="text-gray-400">· {a.category}</span> : null}
            </div>
          </li>
        ))}
        {!articles.length ? <li className={`${dim} py-3`}>기사 없음</li> : null}
      </ul>
    </main>
  );
}
