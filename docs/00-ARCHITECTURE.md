# ebooklib 시스템 아키텍처

> 다른 에이전트/개발자가 시스템을 빠르게 이해할 수 있도록 작성한 문서.

## 시스템 개요

ebooklib은 한국 웹소설을 자동으로 수집 → JSON 저장 → EPUB으로 묶어 → 웹에서 읽고 다운로드할 수 있게 하는 **모노레포 시스템**입니다.

### 핵심 기능
1. **수집**: Cloudflare 보호 사이트(북토끼/bookto31.com, 뉴토끼/toki31.com)에서 챕터 본문 크롤링
2. **저장**: 챕터를 JSON 파일로 `/opt/ai_data/flaresolverr/novels/` 에 저장
3. **읽기**: Next.js 프론트엔드에서 챕터 단위로 표시 (ISR로 CDN 캐시)
4. **EPUB**: 전체 소설을 하나의 EPUB 파일로 묶어서 다운로드 제공 (한글 폰트 임베드)
5. **자동화**: Admin 페이지에서 URL 입력 → 파이프라인 자동 실행

### 비기능 요구사항
- **봇 탐지 회피**: Cloudflare Turnstile을 우회하면서 합법적인 사용자처럼 행동
- **속도 제한**: 같은 사이트에 짧은 간격 연속 요청 방지 (8분 + ±2분 jitter)
- **오프라인 저장**: 수집된 데이터는 로컬에 있어 인터넷 없이도 읽기 가능
- **다국어 표시**: 한국어 본문 + 영문 UI 혼합

---

## 시스템 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                       사용자 브라우저                                │
│  https://miniebook.vercel.app (Vercel CDN)                        │
│  - 소설 목록 / 회차 목록 / 회차 읽기 / EPUB 다운로드 / Admin          │
└─────────────────────────────────────────────────────────────────┘
            │                              ▲
            │ HTTPS                       │ HTTPS (HTML/EPUB)
            ▼                              │
┌─────────────────────────────────────────────────────────────────┐
│                    Vercel CDN (Next.js ISR)                      │
│  /            → 정적 HTML (5분 ISR 갱신, CDN 0ms)                 │
│  /novel/[id]  → ISR 서버 컴포넌트 (CDN 0ms)                       │
│  /admin       → 파이프라인 관리 (URL 입력 → 시작)                  │
│  /api/*       → catch-all 프록시 → devforge                      │
└─────────────────────────────────────────────────────────────────┘
            │
            │ HTTPS (Caddy → nip.io)
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  devforge (Oracle Cloud, 한국)                    │
│  Caddy (host network, auto-HTTPS)                                 │
│    └─ FastAPI (127.0.0.1:8089)                                   │
│        ├─ routers/novels.py    : /api/novels/*                    │
│        ├─ routers/chapters.py  : /api/chapters/{wr_id}            │
│        ├─ routers/metadata.py  : /api/metadata/*                  │
│        └─ routers/pipeline.py  : /api/pipeline/start, /status     │
│                                                                  │
│  FlareSolverr (127.0.0.1:8191) - Cloudflare Turnstile 우회        │
│  Playwright (newtoki AES-GCM 복호화)                              │
└─────────────────────────────────────────────────────────────────┘
            │
            │ 파일 읽기 (data.py → 인덱스 캐시)
            ▼
┌─────────────────────────────────────────────────────────────────┐
│              로컬 데이터 스토리지 (/opt/ai_data/)                  │
│  /opt/ai_data/flaresolverr/novels/{소설명}/                       │
│      ├── meta.json              (소설 메타데이터)                  │
│      ├── {wr_id}.json           (챕터별 본문 - 파일)               │
│      └── _chapters_index.json   (회차 목록 인덱스 캐시)            │
│  /opt/ai_data/flaresolverr/rate_limiter.db                       │
│      (URL별 마지막 요청 시각 기록)                                  │
│  /opt/ai_data/flaresolverr/ebook_watcher/                        │
│      ├── queue.json             (수집 큐)                         │
│      └── pipeline_output.log    (파이프라인 로그)                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 파이프라인 아키텍처

**단일 `pipeline.py`가 5단계 체인으로 동작**합니다.

```
[1] discover ──→ [2] collect ──→ [3] enrich ──→ [4] index ──→ [5] revalidate
   wr_id 발견      source별 fetch   namu.wiki      인덱스 캐시     Vercel ISR
   → 큐 등록      → JSON 저장      메타데이터      재구축          캐시 갱신
                    │
                    └←──────  loop: 반복 ──────┘
```

### 수집기 분기 (source별 Collector)

큐 아이템의 `source` 필드에 따라 collector 자동 분기:

```python
COLLECTORS = {
    "bookto31": _collect_bookto31,  # FlareSolverr + GNUBOARD5 파싱
    "newtoki":  _collect_newtoki,    # Playwright + DataImpulse + AES-GCM 복호화
    "toki31":   _collect_newtoki,    # alias
}
```

### 파이프라인 시작 경로

```
[1] Admin 페이지 (URL 입력)
        │
[2] Vercel /api/pipeline/start (catch-all 프록시)
        │
[3] devforge FastAPI
        ├─ 비밀번호 검증
        ├─ URL 파싱 (bookto31.com → bookto31, toki31.com → newtoki)
        ├─ discover --dry-run → 제목 자동 추출
        ├─ discover → wr_id 큐 등록
        └─ loop 시작 (없으면)
```

---

## 모노레포 디렉토리 구조

```
/opt/workspace/ebooklib/
├── README.md                        # 사용자용 간략 가이드
├── docs/                            # ← 이 문서들이 있는 곳
├── apps/
│   ├── backend/                     # FastAPI Python 서버
│   │   ├── main.py                  # 엔트리포인트 (라우터 등록)
│   │   ├── routers/                 # API 엔드포인트 정의
│   │   │   ├── novels.py
│   │   │   ├── chapters.py
│   │   │   ├── metadata.py
│   │   │   └── pipeline.py          # 파이프라인 시작/상태 API
│   │   ├── services/                # 비즈니스 로직
│   │   │   ├── data.py              # JSON 파일 읽기 (인덱스 캐시)
│   │   │   ├── epub.py              # EPUB 생성 (한글 4폰트 임베드)
│   │   │   ├── bookto31.py          # 북토끼 크롤러 (FlareSolverrSession)
│   │   │   ├── metadata.py          # 메타데이터 검색
│   │   │   └── metadata_namu.py     # namu.wiki 메타데이터
│   │   └── lib/                     # 공통 레이어
│   │       ├── flaresolverr_client.py # FlareSolverr 세션 관리
│   │       ├── storage.py           # 챕터 저장/메타 관리
│   │       ├── rate_limiter.py      # SQLite rate limiter
│   │       └── toki31_playwright.py # 뉴토끼 Playwright 추출기
│   │
│   ├── frontend/                    # Next.js 16 + React 19
│   │   ├── app/
│   │   │   ├── page.tsx             # 라이브러리 메인 (ISR)
│   │   │   ├── admin/page.tsx       # 파이프라인 관리
│   │   │   └── novel/
│   │   │       ├── [id]/page.tsx            # 소설 상세 (ISR)
│   │   │       └── [id]/chapter/[wr_id]/page.tsx  # 챕터 읽기 (ISR)
│   │   ├── lib/
│   │   │   └── api.ts               # fetch 래퍼
│   │   ├── next.config.ts
│   │   └── package.json
│   │
├── scripts/
│   ├── pipeline.py                  # 파이프라인 (discover/collect/enrich/index/revalidate/loop)
│   ├── json_to_epub.py              # 독립 실행 EPUB 변환기
│   └── fonts/                       # EPUB 한글 폰트
```

---

## 주요 모듈 의존성

```
┌────────────────────┐
│ apps/frontend/     │
│   app/novel/[id]/  │
│     page.tsx (ISR) │
└─────────┬──────────┘
          │ GET /api/novels/{id} (devforge 폴백)
          ▼
┌────────────────────┐
│ Vercel /api/[...]  │ ← catch-all 프록시 (모든 요청 devforge로 프록시)
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐    ┌─────────────┐
│ devforge FastAPI   │    │ services/   │
│  routers/novels.py │◄───┤   data.py   │
│                    │    │ - 인덱스 캐시│
└─────────┬──────────┘    └──────┬──────┘
          │                       │
          ▼                       ▼
┌──────────────────────────────────────────┐
│ /opt/ai_data/flaresolverr/novels/{소설}/   │
│   - meta.json + {wr_id}.json + 인덱스     │
└──────────────────────────────────────────┘
```

```
┌────────────────────┐
│ scripts/pipeline.py│ ← 파이프라인 (collect 단계)
└─────────┬──────────┘
          │ source 필드 분기
          ▼
┌────────────────────┐    ┌──────────────────┐
│ _collect_bookto31  │    │ _collect_newtoki │
│ (FlareSolverr)     │    │ (Playwright)     │
└─────────┬──────────┘    └────────┬─────────┘
          │                        │
          ▼                        ▼
┌────────────────────┐    ┌──────────────────┐
│ bookto31.com       │    │ toki31.com       │
│ (Cloudflare)       │    │ (CloudFront+AES) │
└────────────────────┘    └──────────────────┘
```

---

## 데이터 라이프사이클

### 1. 수집 단계 (파이프라인)

**파이프라인 진입점**:
- **Admin 페이지**: `https://miniebook.vercel.app/admin` → URL 입력 → 자동 분기
- **CLI**: `python3 scripts/pipeline.py all <wr_id> <제목>`
- **루프**: `python3 scripts/pipeline.py loop <제목>` (상시 실행)

**파이프라인 단계**:
1. **discover** — 작품 메인 페이지에서 모든 wr_id 발견 → 큐 등록
2. **collect** — 큐에서 하나씩 source별 collector로 fetch → JSON 저장
3. **enrich** — namu.wiki 메타데이터 보강 (1회만, `namu_attempted` 플래그)
4. **index** — `_chapters_index.json` 재구축
5. **revalidate** — Vercel ISR 캐시 갱신

### 2. 저장 단계
- 챕터 JSON 파일을 `/opt/ai_data/flaresolverr/novels/{소설명}/`에 저장
- `data.py`가 인덱스 캐시(`_chapters_index.json`)를 읽어서 API 응답으로 제공

### 3. 읽기 단계
- Next.js 페이지가 ISR로 정적 생성되어 CDN에서 0ms 서빙
- 새 챕터 저장 시 워커가 revalidate API 호출 → 해당 페이지만 재생성

### 4. EPUB 단계
- 사용자가 "EPUB 다운로드" 클릭 → `/api/novels/{id}/epub`
- `epub.py`가 모든 챕터를 모아서 EPUB 바이트 생성
- **4개 한글 폰트 임베드**: NotoSansKR (제목), RIDIBatang (본문), MaruBuri (인용), Literata (영문)

---

## 기술 스택

### 백엔드
- **Python 3.9**
- **FastAPI** - REST API 프레임워크
- **Pydantic** - 데이터 검증
- **ebooklib** - EPUB 생성
- **requests** - HTTP 클라이언트
- **Playwright** - 브라우저 자동화 (뉴토끼 우회)
- **curl_cffi** - TLS fingerprint 위장 (뉴토끼 우회)

### 프론트엔드
- **Next.js 16** - React 풀스택 프레임워크
- **React 19** - UI 라이브러리
- **TypeScript** - 타입 안전성
- **Tailwind CSS** - 스타일링

### 인프라
- **Vercel** - 호스팅 (CDN + ISR)
- **Podman** - 컨테이너 (FlareSolverr)
- **Caddy** - 리버스 프록시 (devforge)
- **Oracle Cloud** - devforge 서버 (한국 리전)

### 외부 의존성
- **FlareSolverr** - 헤드리스 브라우저 - **북토끼 우회**
- **Playwright** - 브라우저 자동화 - **뉴토끼 우회**
- **북토끼** (bookto31.com) - **챕터 본문** SSOT
- **뉴토끼** (toki31.com) - **챕터 본문** 대체 소스
- **namu.wiki** - **메타데이터** + **표지 이미지**
- **문피아** (munpia.com) - **출판사** 정보
- **4개 한글 폰트** - NotoSansKR, RIDIBatang, MaruBuri, Literata

---

## 자동화 계층

```
devforge-watchdog @ 60초마다     ← SERVICE_TARGETS: ebook-watcher 체크 (프로세스+로그 활동)
ebook-watcher.service            ← Type=notify, WatchdogSec=600, Restart=on-watchdog
  └─ pipeline.py loop            ← 상시 실행 (5분 간격 챕터 수집) + sd_notify 신호
```

- **systemd WatchdogSec (1차)**: loop이 5분마다 `WATCHDOG=1` 신호, 10분 내 미수신 시 hang 판정 → `on-watchdog` 재시작
- **devforge-watchdog (2차)**: 60초마다 `check_ebook_pipeline()`으로 프로세스 존재 + 마지막 로그 활동(20분) 확인, hang/죽음 시 재시작
- **queue 무결성**: fcntl 락(collect/queue 분리) + atomic write로 다중 프로세스 동시 접근 시 race 방지
- **실패 보존**: 3회 실패 챕터는 DLQ(failed.json)에 기록 (데이터 손실 방지)

---

## 다음 문서
- [01-DATA-PIPELINE.md](01-DATA-PIPELINE.md) - 데이터 흐름
- [02-BOT-BYPASS.md](02-BOT-BYPASS.md) - 봇 탐지 우회
- [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) - EPUB 생성
- [04-API-REFERENCE.md](04-API-REFERENCE.md) - API 명세
- [05-DEPLOYMENT.md](05-DEPLOYMENT.md) - 배포
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 유지보수
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템
- **DevForge 전체 구조**: `/opt/projects/server/docs/system-architecture.md` — ebook 파이프라인 + watchdog + 데이터 흐름 통합 문서
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템
