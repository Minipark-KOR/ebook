# 변경 이력 (CHANGELOG)

> ebooklib의 모든 주요 변경 사항. 최신이 위.

## 2026-09-06 (최신)

### 본문 터치 네비게이션 개선
- **`chapter/[wr_id]/page.tsx`**: window-level click 이벤트로 변경 (고정 overlay div 제거)
  - 위 15% 터치 → 페이지 업 스크롤 (overlay 없음)
  - 아래 15% 터치 → 페이지 다운 스크롤 (overlay 없음)
  - 가운데 70% 터치 → overlay(이전화/목록/다음화) 토글
  - 링크/버튼 클릭 시 무시 (정상 동작)
  - `e.stopPropagation()` 누락으로 overlay 닫힘 무한루프 버그 수정
  - `window.location.href` → `router.push` (이동 속도 개선)
- **회차 페이지 업/다운**: 4줄 overlap 추가 → 제거 (순수 viewport 단위로 복원)

### 페이지 로딩 속도 최적화
- **`[...slug]/route.ts`**: `proxyToNeon()`에서 `novels/{id}` 호출 시 챕터 1000개 동시 조회 제거
  - 수정 전: 1.9s (챕터 1000개 포함)
  - 수정 후: 0.67s (메타데이터만)
- **`novel/[id]/page.tsx`**: `fetchMetadata(novel.title, "brave")` 제거
  - Brave Search API 타임아웃(30s+)으로 페이지 hang 유발
  - DB 메타데이터(`novel.description`, `genre`, `status`, `publisher`, `namuUrl`) 직접 표시로 대체
- **`fetchChapters()`**: `limit` 기본값 100 → `PAGE_SIZE(20)` 명시 전달 (바로가기 페이지 계산 불일치 해결)
- **회차목록 페이지네이션**: `PAGE_SIZE = 100` → `20`

### 표지 이미지 개선
- **라이브러리 메인 페이지**: 표지 이미지 복원 + 호버 시 메타데이터 오버레이
  - 표지 + 상태 배지 (기본 표시)
  - 호버 시 검은 오버레이 + 제목/작가/설명/장르/화수
- **`image-proxy/route.ts`**: Vercel → devforge 백엔드 경유 (Vercel IP가 namu.wiki CDN에서 403 차단)
  - 응답: `https://devforge.152-69-229-246.nip.io/api/novels/image-proxy?url=...`
- **`novel/[id]/page.tsx`**: `Image` import + coverUrl 관련 코드 전체 제거
- **EPUB 표지 임베드**: `_get_cover_path()`, `_build_cover_html()` 추가
  - `covers/{novel_id}.webp` → EPUB 첫 페이지(cover.xhtml) + `set_cover()`
  - spine 순서: `cover → nav → chap_0001...`

### Vercel 라우팅 수정
- **`[...slug]/route.ts`**: `proxyToNeon()` 조건에 `slug[1] !== 'epub'` && `!== 'image-proxy'` 추가
  - EPUB/image-proxy/chapters 요청이 Neon proxy로 잘못 라우팅 → 501 버그 수정
- **`apps/frontend/app/api/novels/[id]/chapters/route.ts`**: `limit` 기본값 20

### DB 메타데이터 직접 표시
- **`novel/[id]/page.tsx`**: `fetchMetadata()` 제거, `Novel` 인터페이스 필드 직접 표시
  - `description` → 소개글
  - `namuUrl` → 나무위키 링크
  - `publisher` → 출판사
  - `genre`, `status` → 기존 유지
- **`services/data.py`**: `get_novel_detail()`가 `meta.json` 우선 읽도록 수정
  - 수정 전: 항상 `author="미상"`, `coverUrl=null`
  - 수정 후: `meta.json`의 author, description, genre 등 반영

### EPUB 품질 개선
- **`services/epub.py`**:
  - RIDIBatang.otf CSS `format("truetype")` → `format("opentype")` (.otf 자동 감지)
  - `_build_main_css()`: 4개 폰트 각각 `@font-face` 생성
  - EPUB 표지 이미지 임베드 (`covers/` 디렉토리)
  - `set_cover(create_page=False)` + 직접 `EpubHtml` 추가 (중복 방지)

### 시스템 보안 버그 수정
- **`scripts/dual_metadata_ssot.py`**, **`brave_book_url_search.py`**: 하드코딩된 Neon PostgreSQL 연결 문자열 제거
  - `NEON_DATABASE_URL` 환경변수 사용으로 변경
- **`metadata_namu.py`**: `download_cover_to` 매개변수가 함수명 가려 `TypeError` 발생
  - `download_cover_to(...)` → `download_cover(...)` 함수 직접 호출
  - `_fetch_binary()`: `str.startswith(bytes)` 타입오류 수정 (encode 후 bytes 비교)

### ebook-watcher 시스템 개선
- **`ebook-watcher.service`**: `Type=simple` + `Restart=always` + `RestartSec=30` → `Type=oneshot` + `Restart=no`
  - 기존: 236회 재시작 루프
  - 변경: timer(15분)가 트리거, 종료 후 대기
- **`ebook_worker.py`**:
  - `_check_bookto31_alive()`: `requests.get()` → `bookto31._fetch_with_flaresolverr(rate_limit=False)` (FlareSolverr 우회)
  - `save_queue()` 필터 역전 버그 수정: `not any(...)` → `any(...)` (성공한 챕터가 큐에 남고 실패한 게 제거되던 버그)
  - `CHAPTER_DELAY_SEC = 300` (5분)
  - `VENV_PATH` sys.path 추가 제거, `import requests as req` → `import requests`
  - `_trigger_vercel_revalidate()` 불필요한 `is_new_novel` 파라미터 제거
  - meta.json 저장 예외처리 추가
- **`watchdog.py`**: `import requests` 상단으로 이동

### 문서 업데이트
- **docs/ 7개 파일 20건 수정**:
  - GoNotoCurrent → 4개 폰트(NotoSansKR/RIDIBatang/MaruBuri/Literata) 전면 교체
  - `/api/health` → `/health`
  - ebook-watcher.service `Type=simple+Restart=always` → `Type=oneshot+Restart=no`
  - RIDIBatang 라이선스, EPUB 구조 등

## 2026-09-05

### EPUB 다운로드 수정
- **`routers/novels.py`**: `novel_id` 공백→언더스코 변환 (DB 매칭 안 됨 해결)
  - `novel_id.replace(" ", "_")` 적용
  - URL "하남자의 탑 공략법" → DB "하남자의_탑_공략법"
- **`api/[...slug]/route.ts`**: `novels/{id}/epub` catch-all 핸들러 추가 (백엔드로 프록시)
- **별도 route**: `app/api/novels/[id]/epub/route.ts` 분리 (Edge runtime, Vercel 빌드 캐시 회피)
- **해결책**: EPUB 다운로드 시 "Not implemented in Neon proxy" → 정상 작동

### 북토끼 다운 시나리오 - 듀얼 SSOT
- **`scripts/dual_metadata_ssot.py`**: 문피아/조아라 듀얼 SSOT (메타데이터만)
  - 북토끼: 본문 크롤러 (FlareSolverr)
  - 문피아/조아라: 메타데이터 SSOT
  - namu.wiki: 표지/보조
  - 작가/장르/상태 교차 검증, DB + meta.json 업데이트
- **`scripts/ssot_rebuild.py`** → **`dual_metadata_ssot.py`**로 대체
- **og:description 작가 추출 제거**: "작가는 X" 패턴이 다른 책 제목 잡음 (예: "말단병사에서")
  - namu.wiki 메타 행(th/td) 작가 항목이 있을 때만 사용
  - 없으면 "미상" (정직)

### Brave Search로 문피아/조아라 책 URL 매핑
- **`scripts/brave_book_url_search.py`**: 4개 소설의 문피아/조아라 URL 자동 검색
  - `{제목} site:munpia.com` / `{제목} site:joara.com` 검색
  - Brave API 키 로테이션 (secrets.env의 BRAVE_API_KEYS)
  - DB에 `munpia_url`, `joara_url` 컬럼 추가
  - **결과**: 4개 소설 모두 URL 발견 + 저장

### 북토끼 health check
- **`scripts/bookto31_healthcheck.py`**: 북토끼 사이트 상태 체크 (독립 실행 가능)
- **`ebook_worker.py`**: `_check_bookto31_alive()` 함수 (10분 캐시, 죽으면 ebook-watcher 중단)
- **북토끼 다운 시 자동 중단** (ebook_worker.py: _check_bookto31_alive() 실패 시 return, joara fallback은 미구현)

### 표지/회차 라우트 분리
- **Vercel 빌드 캐시 문제 해결**: catch-all `[...slug]/route.ts`의 함수 변경이 캐시됨
- **별도 route로 분리**:
  - `app/api/cover/route.ts` (Node.js runtime, devforge 프록시)
  - `app/api/novels/image-proxy/route.ts` (Node.js runtime, i.namu.wiki 화이트리스트)
  - `app/api/novels/[id]/chapters/route.ts` (Edge runtime, Neon 직접)
  - `app/api/novels/[id]/epub/route.ts` (Edge runtime, devforge 프록시)

### 자동 수집 시스템 (ebook-watcher)
- **`scripts/ebook_watcher/`** (큐 기반 워커):
  - `watchdog.py` - 1분마다 큐 체크, ebook-worker 트리거 (내부 TRIGGER_INTERVAL_SEC=60)
  - `ebook_worker.py` - 북토끼에서 챕터 fetch → DB 저장 → Neon 동기화
  - `ebook_queue.py` - CLI 큐 관리 (add/list/remove)
- **북토끼 health check** 통합: 죽으면 자동 중단
- **CHAPTER_DELAY_SEC**: 1분 고정 (코드 48행: `CHAPTER_DELAY_SEC = 60`)
- **큐 형식**: `[{"wr_id": 12345, "novel_title": "제목", "priority": 1-5, "added_at": "..."}]`

### 챕터 자동 발견 스크립트
- **`scripts/discover_chapters.py`**: 북토끼 작품 메인 페이지에서 모든 spage 순회, 회차 wr_id 자동 추출
  - 839화 한 작품 약 28 spage × 30 = 약 6분
  - **사용법**: `python3 discover_chapters.py 25575 "오늘만 사는 기사"`
  - 큐에 일괄 추가

### 메타데이터 동기화
- **`scripts/ebook_sync.py`**: 로컬 DB → Neon DB 동기화
  - `sync_all_novels()` - 모든 로컬 소설/챕터 UPSERT
  - `query_novels_from_neon()`, `query_chapter_from_neon()` - Vercel 측 조회 함수
  - **`_infer_status_from_db()`**: 마지막 챕터 collected_at 기준 상태 추정 (14일 기준)
- **DB 스키마**:
  - `ebook_novels (id, title, author, total_chapters, cover_url, description, genre[], status, publisher, namu_url, munpia_url, joara_url, updated_at)`
  - `ebook_chapters (wr_id, novel_id, chapter, title, content_length, content, bookto_url, collected_at)`

### EPUB 생성 (services/epub.py)
- **4개 폰트 임베드**:
  - `NotoSansKR-Regular.ttf` (한글 고딕 - 제목)
  - `RIDIBatang.otf` (한글 세리프 - 본문)
  - `MaruBuri-Regular.ttf` (한글 둥근고딕 - 인용)
  - `Literata-Variable.ttf` (영문 세리프)
- **스타일**: `@font-face` + 챕터에서 `link rel="stylesheet" href="../styles/main.css"`
- **위치**: `/opt/workspace/ebooklib/scripts/fonts/`
- **버그 수정**: `from ebooklib import epub` → `from ebooklib.epub import EpubBook, EpubHtml, ...` (ebooklib 0.20에서 epub 모듈 직접 import 안 됨)

### UI 정리
- **`page.tsx` (메인)**:
  - 소설 카드: 표지 + 작가 + 장르 + 상태 배지
  - 상태별 색상: 완결(gray), 연재중(blue), 단편(purple)
  - "(완)" 접미사 제거 (사용자 요청)
- **`novel/[id]/page.tsx` (상세)**:
  - 표지 + 줄거리(description) 제거
  - "표지가 있으니 굳이 소개글은 없어도 되" (사용자 요청)
  - 제목 + 작가 + 장르 + 상태 + 회차 목록 + EPUB 다운로드

### 의존성 추가
- `apps/backend/requirements.txt`에 추가:
  - `psycopg2-binary>=2.9.0` (Neon 동기화)
  - `ebooklib>=0.20` (EPUB 생성)
  - `lxml>=6.0.0` (ebooklib 의존성)
  - `curl_cffi>=0.13.0` (FlareSolverr 대안 - 미사용)
- `apps/frontend/package.json`에 추가:
  - `@neondatabase/serverless>=1.1.0` (Vercel Edge + Neon)
- venv 재생성: ebooklib 직접 설치 (system python 사용 안 함, 진짜 venv)

## 시스템 아키텍처

### 듀얼 SSOT 구조
```
북토끼 (bookto31.com)        → 챕터 본문
문피아 (munpia.com)         → 메타데이터 SSOT (Brave Search로 URL 검색)
조아라 (joara.com)          → 메타데이터 SSOT (보조)
namu.wiki (namu.wiki)        → 표지 이미지 백업
```

### 데이터 흐름
1. **수집**: ebook-watcher (15분마다) → 북토끼에서 챕터 본문 + namu.wiki에서 표지/메타
2. **저장**: 로컬 JSON (`/opt/ai_data/flaresolverr/novels/{소설ID}/`)
3. **동기화**: ebook_sync.py → Neon DB UPSERT (PostgreSQL 16)
4. **표시**: Vercel → Neon 직접 query (Edge runtime) → 200-500ms 응답
5. **갱신**: ebook_watcher가 챕터 저장 후 → Vercel revalidate API 호출 (즉시)
6. **EPUB**: 사용자 요청 시 build_epub() → 4개 폰트 + 챕터 HTML

### 보호 계층 (4중)
- **Layer 3**: `ebook-watcher.timer (*:0/15)` - 15분마다 자동 트리거
- **Layer 2**: `ebook-watcher.service` - Restart=always (systemd)
- **Layer 1**: `devforge-watchdog` (60초마다) - 서비스 죽으면 자동 재시작
- **Layer 0**: `systemd Restart=always` - watchdog도 자동 재시작

## 핵심 파일 위치
- **백엔드**: `/opt/workspace/ebooklib/apps/backend/`
- **프론트엔드**: `/opt/workspace/ebooklib/apps/frontend/`
- **데이터**: `/opt/ai_data/flaresolverr/`
  - `novels/{소설ID}/{wr_id}.json` (챕터)
  - `rate_limiter.db` (북토끼 rate limit)
  - `ebook_watcher/` (큐 + 로그)
  - `covers/` (표지)
- **폰트**: `/opt/workspace/ebooklib/scripts/fonts/`
- **문서**: `/opt/workspace/ebooklib/docs/`
- **유틸리티**: `/opt/workspace/ebooklib/scripts/`

## 운영 명령
- **수동 큐 추가**: `python3 scripts/ebook_watcher/ebook_queue.py add <wr_id> "제목"`
- **워처 상태**: `systemctl --user status ebook-watcher.timer`
- **북토끼 health**: `python3 scripts/bookto31_healthcheck.py`
- **듀얼 SSOT 메타 갱신**: `python3 scripts/dual_metadata_ssot.py`
- **수동 발견**: `python3 scripts/discover_chapters.py <작품_메인_wr_id> "제목"`
- **DB → Neon 동기화**: `python3 scripts/ebook_sync.py` (NEON_DATABASE_URL 환경변수 필요)
- **로컬 백엔드 재시작**: `systemctl --user restart ebook-api.service`

## 현재 ebooklib 상태 (4개 소설)
- **하남자의 탑 공략법** (557화, 완결, 작가=꾸찌꾸찌)
- **오늘만 사는 기사** (1화, 신규, 작가=미상, munpia_url + joara_url)
- **게임 속 바바리안으로 살아남기** (1화, 신규, 작가=미상, munpia_url + joara_url)
- **화산귀환** (1화, 신규, 작가=태존비록, munpia_url + joara_url)
- 2개 테스트 아이템 큐에 있음 (839챕터는 discover_chapters.py 미실행으로 큐 미추가)