# 데이터 파이프라인

> 챕터 데이터가 외부 사이트 → 로컬 DB → API 응답으로 흘러가는 과정.

## 파이프라인 흐름

```
[외부 소스]                  [수집]                  [저장]                 [API]
─────────                ─────────              ───────────           ────────────
북토끼 (bookto31.com)    ─┐
                         │
뉴토끼 (toki31.com)      ─┼─→  services/bookto31.py  ─→  /opt/ai_data/   ─→  services/data.py
                         │      services/toki31.py       flaresolverr/         (glob)
miniebook.vercel.app     ─┘                                  novels/{소설명}/
     (자체 API)                       ─────────                ├── meta.json
                                      (rate limiter:           └── {wr_id}.json
                                       8분 + ±2분)              (557 챕터)
```

## 1. 데이터 소스 (외부)

### 1.1 miniebook.vercel.app (자체 DB, 가장 안전)
- URL: `https://miniebook.vercel.app`
- 백엔드: 자체 호스팅 (Vercel)
- 인증: **없음** (공개 API)
- 챕터 메타: `GET /api/novels/{id}/chapters?page=N&limit=100`
- 챕터 본문: `GET /api/chapters/{wr_id}` (content 필드)
- 우회 필요: 없음
- 권장: **기본 데이터 소스**

### 1.2 북토끼 (bookto31.com)
- URL: `https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={wr_id}&spage=N`
- 백엔드: GNUBOARD5 + APMS 테마
- 인증: 없음 (단, Cloudflare Turnstile Challenge)
- 챕터 본문: HTML `<div class="view-content book-text-viewer">` 안
- 우회 필요: **FlareSolverr**
- 권장: miniebook에서 빈 응답인 챕터만 fallback

### 1.3 뉴토끼 (toki31.com) - 비권장
- URL: `https://toki31.com/novel/{id}/{wr_id}`
- 백엔드: Next.js + CloudFront
- 인증: 회차 본문은 PATCH 로그인 필요 (봇 차단)
- 우회 필요: **residential proxy** + curl_cffi TLS 위장
- 권장: 사용하지 않음 (북토끼에 동일 데이터 있음)

## 2. 수집 (services/)

### 2.1 services/bookto31.py - 북토끼 크롤러

**함수**:
- `fetch_home()` - 북토끼 홈 페이지
- `fetch_novel_index(wr_id: int)` - 작품 메인 페이지 (회차 목록)
- `fetch_search(q: str)` - 검색 결과 페이지
- `fetch_chapter(wr_id: int)` - 회차 본문 페이지 (HTML)
- `_fetch_with_flaresolverr(url, rate_limit=True)` - FlareSolverr로 challenge 우회 (public API 유지)
- `parse_chapter_list(html, novel_id)` - 작품 페이지에서 회차 목록 추출
- `parse_chapter_body(html)` - 회차 본문 추출 (`view-content book-text-viewer`)
- `parse_novel_meta(html)` - 제목/작가/설명 추출

**Rate limiting**:
- `FlareSolverrSession(rate_limit=True)` (기본값): 같은 URL에 8분 + ±2분 jitter 자동 대기
- `FlareSolverrSession(rate_limit=False)`: namu.wiki, discover_chapters 등에서 사용

**FlareSolverr 통신**:
```python
# lib/flaresolverr_client.py의 FlareSolverrSession 사용
from lib.flaresolverr_client import FlareSolverrSession

_fs = FlareSolverrSession(rate_limit=True)
html = _fs.fetch("https://bookto31.com/...")
```

### 2.2 services/toki31.py - 뉴토끼 크롤러

**변경 이력 (2026-09-06)**:
- requests+proxy → curl_cffi (TLS fingerprint 위장)
- proxy pool 관리 로직 전체 제거
- Next.js RSC payload 파서 추가

**함수**:
- `fetch_home()` - 뉴토끼 홈 페이지
- `fetch_ing()` - 연재중 웹툰/소설 목록
- `fetch_novel_list()` - 소설 목록
- `parse_rsc_payload(html)` - RSC payload에서 에피소드 데이터 추출
- `extract_episode_data(html)` - 에피소드 데이터 추출

**curl_cffi 사용**:
```python
# lib/curl_session.py의 create_curl_session 사용
from lib.curl_session import create_curl_session

_session = create_curl_session(impersonate="chrome131")
resp = _session.get("https://toki31.com/novel", timeout=15)
```

### 2.3 services/data.py - 데이터 읽기

**함수**:
- `get_novel_list()` - 모든 소설 메타 (`*.json` 파일 glob)
- `get_novel_detail(novel_id)` - 특정 소설 메타
- `get_chapter_list(novel_id, page, limit)` - 회차 목록 (페이지네이션)
- `get_chapter_detail(wr_id)` - 특정 회차 본문

**데이터 디렉토리**: `/opt/ai_data/flaresolverr/novels/{소설ID}/`
- 디렉토리명 = 소설ID (예: `하남자의_탑_공략법`)
- 메타: `meta.json`
- 챕터: `{wr_id}.json` (wr_id는 정수)

## 3. 저장 (JSON 스키마)

### 3.1 meta.json

```json
{
  "id": "하남자의_탑_공략법",
  "title": "하남자의 탑 공략법",
  "author": "미상",
  "totalChapters": 557,
  "coverUrl": null
}
```

### 3.2 {wr_id}.json (챕터)

```json
{
  "wr_id": 21431,
  "chapter": 1,
  "title": "하남자의 탑 공략법 - 1화",
  "content_length": 5804,
  "content": "1화\n2004년.\n지구 곳곳에 거대한 검은 탑...",
  "url": "https://bookto31.com/bbs/board.php?bo_table=novel&wr_id=21431",
  "collected_at": "2026-09-01T09:26:51.650051+09:00",
  "user_agent": "Mozilla/5.0 ..."
}
```

## 4. API 응답 변환 (data.py)

```python
def get_chapter_list(novel_id, page=1, limit=20):
    novel_dir = DATA_DIR / novel_id
    chapters = []
    for json_file in sorted(novel_dir.glob("*.json")):
        if not json_file.stem.isdigit():
            continue
        with open(json_file) as f:
            data = json.load(f)
        chapters.append({
            "wr_id": data.get("wr_id"),
            "chapter": data.get("chapter"),
            "title": data.get("title"),
            "contentLength": data.get("content_length"),
        })
    # 페이지네이션
    start = (page - 1) * limit
    end = start + limit
    return {
        "data": chapters[start:end],
        "pagination": {"page": page, "limit": limit, "total": len(chapters)},
    }
```

## 5. 데이터 흐름 (한 챕터 기준)

```
1. 사용자가 회차 클릭
   └─→ GET /novel/{id}/chapter/{wr_id} (프론트)

2. 프론트: fetchChapter(wr_id)
   └─→ GET /api/chapters/{wr_id} (백엔드)

3. 백엔드: get_chapter_detail(wr_id) (data.py)
   └─→ /opt/ai_data/.../{wr_id}.json 읽기

4. 응답: {wr_id, chapter, title, content, prevChapter, nextChapter, ...}

5. 프론트: 본문 렌더링 + 이전/다음 회차 네비게이션
```

## 6. 일괄 수집 스크립트 (예시)

```python
# services/bookto31.py의 API 사용
from services.bookto31 import fetch_novel_index, fetch_chapter, parse_chapter_list, parse_chapter_body

# 작품 페이지 (1회만)
index_html = fetch_novel_index(21430)  # rate_limit 적용됨
chapters = parse_chapter_list(index_html, novel_id=21430)

# 각 챕터 (8분 간격 자동)
for ch in chapters:
    html = fetch_chapter(ch['wr_id'])  # 같은 URL → 자동 대기
    body = parse_chapter_body(html)
    # lib.storage.save_chapter()로 저장...
```

```python
# lib.storage를 사용한 저장
from lib.storage import save_chapter, update_meta_from_namu

# 챕터 저장
save_chapter("오늘만_사는_기사", wr_id=25575, body="본문...", source="bookto31")

# namu.wiki 메타데이터 업데이트
update_meta_from_namu("오늘만_사는_기사", {
    "author": "작가명",
    "description": "소개글",
    "genre": ["판타지"],
    "status": "연재중",
})
```

## 7. 데이터 무결성 보장

### 챕터 URL 매핑
- `wr_id - 21430 = chapter` (작품 메인 페이지가 21430, 1화가 21431)
- 예외: 21829, 21849, 21852, 21891, 21939 (북토끼 직접 수집분)

### 빈 챕터 감지
```python
if not content or 'Connection timed out' in title or '522' in title:
    # 북토끼 fallback
    html = fetch_chapter(wr_id)
    ...
```

### 갱신 확인
- miniebook API로 페이지 1만 호출해서 `total` 비교
- `total > 현재 DB 챕터 수`면 신규 챕터 존재

## 다음 문서
- [02-BOT-BYPASS.md](02-BOT-BYPASS.md) - 봇 우회 전략
- [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) - EPUB 생성