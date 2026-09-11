// devforge API 응답 타입 (서버 컴포넌트에서 fetch로 사용).
// 참고: /api/[...slug] catch-all 프록시가 경로 세그먼트를 URL-decode하므로
//       클라이언트는 raw(미인코딩) 한글 ID를 보낸다.

export type MediaType = "novel" | "comic" | "webtoon";

export interface Novel {
  id: string;
  title: string;
  author: string;
  totalChapters: number;
  coverUrl: string | null;
  description?: string;
  genre?: string[];
  status?: string;
  publisher?: string;
  namuUrl?: string;
  mediaType?: MediaType;
}

export interface Chapter {
  wr_id: number;
  chapter: number;
  title: string;
  contentLength: number;
}

export interface ChapterDetail {
  wr_id: number;
  chapter: number;
  title: string;
  content: string;
  images: string[];
  prevChapter: number | null;
  nextChapter: number | null;
}
