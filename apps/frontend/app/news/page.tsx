import { getJSON, NewsItem } from "@/lib/server";
import NewsView from "./NewsView";

// ISR: 최신 뉴스 기본 뷰 (엣지 캐시).
export const revalidate = 300;

export default async function NewsPage() {
  const [articles, dates] = await Promise.all([
    getJSON<NewsItem[]>("/news/articles", 300),
    getJSON<{ date: string }[]>("/news/dates", 300),
  ]);
  const date = dates?.[0]?.date;
  return <NewsView date={date} dates={dates || []} articles={articles || []} />;
}
