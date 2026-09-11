import { getJSON, NewsItem } from "@/lib/server";
import NewsView from "../NewsView";

// ISR: 날짜별 뉴스 (알려진 날짜는 사전 렌더, 그 외는 on-demand 후 캐시).
export const revalidate = 300;
export const dynamicParams = true;

export async function generateStaticParams() {
  const dates = (await getJSON<{ date: string }[]>("/news/dates", 300)) || [];
  return dates.slice(0, 12).map((d) => ({ date: d.date }));
}

export default async function NewsDatePage({
  params,
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;
  const [articles, dates] = await Promise.all([
    getJSON<NewsItem[]>(`/news/articles?date=${date}`, 300),
    getJSON<{ date: string }[]>("/news/dates", 300),
  ]);
  return <NewsView date={date} dates={dates || []} articles={articles || []} />;
}
