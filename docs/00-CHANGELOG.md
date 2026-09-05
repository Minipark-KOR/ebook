# 변경 이력 (CHANGELOG)

> ebooklib의 모든 주요 변경 사항. 최신이 위.

## 2026-09-05 (가장 최근)

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
- **북토끼 다운 시 자동 중단 + 대안 소스 시도 (joara)** 가능

### 표지/회차 라우트 분리
- **Vercel 빌드 캐시 문제 해결**: catch-all `[...slug]/route.ts`의 함수 변경이 캐시됨
- **별도 route로 분리**:
  - `app/api/cover/route.ts` (Node.js runtime, devforge 프록시)
  - `app/api/novels/image-proxy/route.ts` (Node.js runtime, i.namu.wiki 화이트리스트)
  - `app/api/novels/[id]/chapters/route.ts` (Edge runtime, Neon 직접)
  - `app/api/novels/[id]/epub/route.ts` (Edge runtime, devforge 프록시)

### 자동 수집 시스템 (ebook-watcher)
- **`scripts/ebook_watcher/`** (큐 기반 워커):
  - `watchdog.py` - 15분마다 큐 체크, ebook-worker 트리거
  - `ebook_worker.py` - 북토끼에서 챕터 fetch → DB 저장 → Neon 동기화
  - `ebook_queue.py` - CLI 큐 관리 (add/list/remove)
- **북토끼 health check** 통합: 죽으면 자동 중단
- **CHAPTER_DELAY_SEC**: 기본 5분, 임시 1분 (대량 수집용, 수집 완료 후 5분 복원 권장)
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

### 보호 계층
- **3중 자동화 보호**:
  - `ebook-watcher.timer (*:0/15)` - 15분마다 자동 트리거
  - `devforge-watchdog` (60초마다) - 서비스 죽으면 자동 재시작
  - `Restart=on-failure` SystemD - ebook-api 충돌 시 재시작

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
- **게임 속 바바리안** (1화, 신규, 작가=미상, munpia_url + joara_url)
- **화산귀환** (1화, 신규, 작가=태존비록, munpia_url + joara_url)
- 839챕터 큐에 있음 (오늘만 사는 기사, ebook-watcher가 점진 수집 중)