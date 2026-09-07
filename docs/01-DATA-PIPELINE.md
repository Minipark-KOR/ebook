# 데이터 파이프라인

> 챕터 데이터가 외부 사이트 → 로컬 JSON → API 응답으로 흘러가는 과정.

## 파이프라인 흐름

```
[외부 소스]                    [수집]                  [저장]                 [API]
─────────                  ─────────              ───────────           ────────────
북토끼 (bookto31.com)      ─┐
                            │
뉴토끼 (toki31.com)        ─┼─→  pipeline.py collect  ─→  /opt/ai_data/  ─→  FastAPI
                            │       (source별 분기)        flaresolverr/       (3ms)
                            │                              novels/{소설명}/
                            │   ─────────                    ├── meta.json
                            │   (rate limiter:               ├── {wr_id}.json
                            │    8분 + ±2분)                  └── _chapters_index.json
                            │
                            └──→  Vercel ISR (CDN 0ms)
```

## 1. 파이프라인 시작

### Admin 페이지 (권장)
- URL: `https://miniebook.vercel.app/admin`
- 비밀번호 입력 + URL 붙여넣기
- 자동 분기: bookto31.com → bookto31, toki31.com → newtoki
- 제목 자동 추출 → 큐 등록 → 루프 시작

### CLI
```bash
# 전체 체인 (신규 소설)
python3 scripts/pipeline.py all 25575 "오늘만 사는 기사"

# 무한 루프 (기존 소설)
python3 scripts/pipeline.py loop "오늘만 사는 기사"

# 단계별
python3 scripts/pipeline.py discover 25575 "오늘만 사는 기사" --source bookto31
python3 scripts/pipeline.py collect --limit 1 --source bookto31
python3 scripts/pipeline.py enrich "오늘만 사는 기사"
python3 scripts/pipeline.py index
python3 scripts/pipeline.py revalidate "오늘만 사는 기사"
```

## 2. 파이프라인 단계

### Step 1: discover
- 작품 메인 페이지에서 GNUBOARD5 spage 순회
- 모든 회차의 wr_id + chapter 번호 추출 → `queue.json`에 등록
- `--dry-run` 모드로 제목만 추출 가능

### Step 2: collect (source별 분기)

큐 아이템의 `source` 필드에 따라 collector 자동 선택:

| source | collector | 방법 | 속도 |
|--------|-----------|------|------|
| `bookto31` | `_collect_bookto31()` | FlareSolverr + HTML 파싱 | 5분/챕터 |
| `newtoki` | `_collect_newtoki()` | Playwright + AES-GCM 복호화 | ~30초/챕터 |

**bookto31 수집기**:
```python
# services/bookto31.py 사용
html = fetch_chapter(wr_id)            # FlareSolverr → Cloudflare 우회
body = parse_chapter_body(html)        # GNUBOARD5 본문 추출
chapter_num = extract_from_title(html) # "제목 - 839화" 패턴
```

**newtoki 수집기**:
```python
# lib/toki31_playwright.py 사용
result = await fetch_chapter_content_full(novel_id, episode_id)
# Playwright 브라우저가 ad/ack 처리 → API 응답 인터셉트 → AES-GCM 복호화
```

### Step 3: enrich
- namu.wiki에서 작가/표지/장르/설명/연재상태 수집
- `namu_attempted` 플래그로 **1회만** 시도 (중복 방지)
- 표지 이미지 검증: 5KB 이상, 200×200px 이상, og:image만 사용

### Step 4: index
- `_chapters_index.json` 재구축
- API 성능 최적화 (모든 파일 열지 않음)

### Step 5: revalidate
- Vercel ISR 캐시 갱신 (해당 소설 페이지만)
- `VERCEL_REVALIDATE_URL` + `VERCEL_REVALIDATE_TOKEN` 필요

## 3. 데이터 저장 (JSON 스키마)

### meta.json
```json
{
  "id": "하남자의_탑_공략법",
  "title": "하남자의 탑 공략법",
  "author": "꾸찌꾸찌",
  "totalChapters": 557,
  "coverUrl": "/api/novels/image-proxy?url=...",
  "description": "한국의 현대 판타지 웹소설...",
  "genre": ["현대 판타지", "헌터물", "탑등반물"],
  "status": "완결",
  "publisher": "문피아",
  "namuUrl": "https://namu.wiki/w/...",
  "namu_attempted": true
}
```

### {wr_id}.json (챕터)
```json
{
  "wr_id": 21431,
  "chapter": 1,
  "title": "하남자의 탑 공략법 - 1화",
  "content_length": 5804,
  "content": "1화\n2004년.\n지구 곳곳에 거대한 검은 탑...",
  "url": "https://bookto31.com/...",
  "collected_at": "2026-09-01T09:26:51+09:00",
  "source": "bookto31"
}
```

### _chapters_index.json (인덱스 캐시)
```json
{
  "updated_at": "2026-09-07T...",
  "chapters": [
    {"wr_id": 21431, "chapter": 1, "title": "...", "contentLength": 5804},
    ...
  ]
}
```

## 4. 데이터 읽기 (API)

```python
def get_chapter_list(novel_id, page=1, limit=20):
    novel_dir = DATA_DIR / novel_id
    # 인덱스 캐시 사용 (요청마다 파일 전체 로드 방지)
    chapters = load_chapters_index(novel_dir)
    start = (page - 1) * limit
    end = start + limit
    return {
        "data": chapters[start:end],
        "pagination": {"page": page, "limit": limit, "total": len(chapters)},
    }
```

## 5. 데이터 흐름 (한 챕터 기준)

```
1. 파이프라인 collect 단계 (devforge)
   └─→ source별 collector → JSON 저장
   └─→ index 재구축 → revalidate 호출
        ↓

2. Vercel ISR (CDN)
   └─→ 해당 소설 페이지 백그라운드 재생성
   └─→ 사용자는 항상 CDN 캐시 (0ms)
        ↓

3. 사용자가 회차 클릭
   └─→ GET /novel/{id}/chapter/{wr_id} (ISR)
   └─→ devforge 폴백: /api/chapters/{wr_id} (3ms)
```

## 6. 데이터 무결성

- **챕터 번호**: HTML `<title>`에서 `"제목 - N화"` 패턴 추출
- **외전 챕터**: 본문이 `"외전 N화"`로 시작 → wr_id 기준 추정값 사용
- **빈 챕터**: 100자 미만 본문은 수집 실패로 간주, 3회 재시도

## 7. 큐 데이터 구조

```json
{
  "wr_id": 26400,
  "novel_title": "오늘만 사는 기사",
  "chapter": 821,
  "source": "bookto31",
  "priority": 5,
  "added_at": "2026-09-07T...",
  "attempts": 0,
  "last_error": null
}
```

## 다음 문서
- [02-BOT-BYPASS.md](02-BOT-BYPASS.md) - 봇 우회 전략
- [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) - EPUB 생성