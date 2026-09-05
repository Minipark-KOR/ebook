# REST API 명세

> 백엔드 FastAPI가 제공하는 모든 엔드포인트.

## Base URL
- 개발: `http://localhost:8000`
- 프로덕션: `https://miniebook.vercel.app`

## 응답 형식
- JSON
- Content-Type: `application/json` (또는 EPUB의 경우 `application/epub+zip`)
- 한글이 포함된 문자열은 UTF-8

## 엔드포인트

### 1. GET /health

헬스체크. (라우터 prefix 없이 루트 앱에 직접 등록)

**응답**:
```json
{"status": "ok"}
```

### 2. GET /api/novels

모든 소설 목록.

**응답**:
```json
{
  "novels": [
    {
      "id": "하남자의_탑_공략법",
      "title": "하남자의 탑 공략법",
      "author": "미상",
      "totalChapters": 557,
      "coverUrl": null
    }
  ]
}
```

### 3. GET /api/novels/{novel_id}

특정 소설 상세.

**경로 파라미터**:
- `novel_id` (str): 소설 ID (예: `하남자의_탑_공략법`, URL 인코딩 필요)

**응답**:
```json
{
  "id": "하남자의_탑_공략법",
  "title": "하남자의 탑 공략법",
  "author": "미상",
  "totalChapters": 557,
  "coverUrl": null
}
```

**에러**:
- 404: 소설을 찾을 수 없음

### 4. GET /api/novels/{novel_id}/chapters

특정 소설의 회차 목록 (페이지네이션).

**경로 파라미터**:
- `novel_id` (str): 소설 ID

**쿼리 파라미터**:
- `page` (int, 기본 1): 페이지 번호
- `limit` (int, 기본 20): 페이지당 회차 수

**응답**:
```json
{
  "data": [
    {
      "wr_id": 21431,
      "chapter": 1,
      "title": "하남자의 탑 공략법 - 1화",
      "contentLength": 5804
    },
    ...
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 557
  }
}
```

### 5. GET /api/chapters/{wr_id}

특정 회차 본문.

**경로 파라미터**:
- `wr_id` (int): 회차 ID (예: 21431)

**응답**:
```json
{
  "wr_id": 21431,
  "chapter": 1,
  "title": "하남자의 탑 공략법 - 1화",
  "content": "1화\n2004년.\n지구 곳곳에 거대한 검은 탑...",
  "images": [],
  "prevChapter": null,
  "nextChapter": 21432
}
```

**참고**:
- `chapter`: 북토끼에서 직접 수집한 챕터는 `null` (wr_id-21430 매핑 실패)
- `prevChapter`/`nextChapter`: 같은 소설 내 인접 wr_id (정확한 회차 순서 아닐 수 있음)

### 6. GET /api/novels/{novel_id}/epub

**EPUB 다운로드**.

**경로 파라미터**:
- `novel_id` (str): 소설 ID

**응답**:
- Content-Type: `application/epub+zip`
- Content-Disposition: `attachment; filename*=UTF-8''{제목}.epub`
- 본문: EPUB 바이트 (12MB+)
- 한글 폰트 임베드됨

**에러**:
- 404: 소설을 찾을 수 없음
- 500: 챕터 데이터 없음 (EPUB 생성 실패)

### 7. GET /api/metadata/lookup

단일 메타데이터 조회 (ISBNlib).

**쿼리 파라미터**:
- `title` (str): 소설 제목

**응답**:
```json
{
  "title": "...",
  "authors": ["..."],
  "publisher": "...",
  "year": "...",
  "isbn13": "...",
  "isbn10": "...",
  "language": "...",
  "coverUrl": null,
  "description": "...",
  "subjects": ["..."],
  "pageCount": null,
  "source": "goob"
}
```

### 8. GET /api/metadata/search

다중 메타데이터 검색.

**쿼리 파라미터**:
- `title` (str): 소설 제목
- `service` (str): `search` 서비스 (기본: `brave`)
- `max_results` (int, 기본 5): 최대 결과 수

**응답**:
```json
[
  {
    "title": "...",
    "authors": ["..."],
    "publisher": "...",
    "year": "2024",
    "isbn13": null,
    "isbn10": null,
    "language": "ko",
    "coverUrl": null,
    "description": "...",
    "subjects": ["판타지"],
    "pageCount": null,
    "source": "brave"
  }
]
```

## 메타데이터 서비스

`/api/metadata/lookup`과 `/api/metadata/search`는 다음 서비스를 지원:

- `goob` (Google Books) - 공식 ISBN 검색
- `openl` (OpenLibrary) - 공식 ISBN 검색
- `brave` (Brave Search + DuckDuckGo fallback) - 한국 웹소설에 권장

**한국어 웹소설은 `brave` 사용 권장**.

## 에러 응답

모든 에러는 다음 형식:
```json
{"detail": "에러 메시지"}
```

상태 코드:
- 400: 잘못된 요청
- 404: 리소스 없음
- 500: 서버 오류

## CORS

- 프로덕션 (Vercel): 자동 처리 (같은 도메인)
- 개발 (`localhost:3000` → `localhost:8000`): `CORS_ORIGINS` 환경변수 설정 필요

## 다음 문서
- [05-DEPLOYMENT.md](05-DEPLOYMENT.md) - 배포