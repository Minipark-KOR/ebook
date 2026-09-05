# ebooklib 시스템 아키텍처

> 다른 에이전트/개발자가 시스템을 빠르게 이해할 수 있도록 작성한 문서.

## 시스템 개요

ebooklib은 한국 웹소설을 자동으로 수집 → DB 저장 → EPUB으로 묶어 → 웹에서 읽고 다운로드할 수 있게 하는 **모노레포 시스템**입니다.

### 핵심 기능
1. **수집**: Cloudflare 보호 사이트(북토끼/bookto31.com, 뉴토끼/toki31.com)에서 챕터 본문 크롤링
2. **저장**: 챕터를 JSON 파일로 `/opt/ai_data/flaresolverr/novels/` 에 저장
3. **읽기**: Next.js 프론트엔드에서 챕터 단위로 표시
4. **EPUB**: 전체 소설을 하나의 EPUB 파일로 묶어서 다운로드 제공 (한글 폰트 임베드)

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
│  - 소설 목록 / 회차 목록 / 회차 읽기 / EPUB 다운로드                  │
└─────────────────────────────────────────────────────────────────┘
            │                              ▲
            │ HTTPS                       │ HTTPS (HTML/EPUB)
            ▼                              │
┌─────────────────────────────────────────────────────────────────┐
│                     Vercel CDN / Cloudflare                       │
│  /api/* → FastAPI Python Serverless Functions                    │
│  /*     → Next.js Static Build                                  │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (Vercel)                      │
│  apps/backend/main.py                                           │
│  - routers/novels.py    : /api/novels/*                         │
│  - routers/chapters.py  : /api/novels/{id}/chapters, /api/chapters/{wr_id} │
│  - routers/metadata.py  : /api/metadata/* (Brave/GoogLe/DuckDuckGo) │
│                                                                  │
│  - services/data.py      : JSON 파일 읽기                         │
│  - services/epub.py     : EPUB 생성 (GoNoto 폰트 임베드)            │
│  - services/bookto31.py : 북토끼 크롤러 (Cloudflare 우회)            │
│  - services/toki31.py   : 뉴토끼 크롤러 (Residential Proxy)         │
│  - services/metadata.py : 메타데이터 조회                          │
│  - lib/rate_limiter.py  : 8분 + ±2분 jitter rate limiting            │
└─────────────────────────────────────────────────────────────────┘
            │
            │ 파일 읽기 (data.py → glob)
            ▼
┌─────────────────────────────────────────────────────────────────┐
│              로컬 데이터 스토리지 (/opt/ai_data/)                  │
│  /opt/ai_data/flaresolverr/novels/{소설명}/                       │
│      ├── meta.json     (소설 메타데이터)                             │
│      ├── {wr_id}.json  (챕터별 본문 - 557개+ 파일)                  │
│      └── episode_ids.json                                       │
│  /opt/ai_data/flaresolverr/rate_limiter.db                      │
│      (URL별 마지막 요청 시각 기록)                                  │
└─────────────────────────────────────────────────────────────────┘

                  ▲                  ▲                  ▲
                  │                  │                  │
                  │ FlareSolverr 우회 │                  │
                  │ (FlareSolverr API)                  │
                  │                  │                  │
┌──────────────────────────┐  ┌────────────────┐  ┌───────────────┐
│  bookto31.com (북토끼)      │  │ toki31.com     │  │ miniebook.     │
│  - Cloudflare Turnstile  │  │ - CloudFront    │  │ vercel.app     │
│  - GNUBOARD5 + APMS 테마  │  │ - Next.js       │  │ (자체 DB)       │
│  - 557회차 데이터 소스     │  │ - 일부 데이터  │  │ 553챕터       │
└──────────────────────────┘  └────────────────┘  └───────────────┘
            ▲
            │ (FlareSolverr가 challenge 풀어서 우회)
            │
┌─────────────────────────────────────────────────────────────────┐
│              FlareSolverr (127.0.0.1:8191)                       │
│  ARM64 컨테이너 - 헤드리스 브라우저로 challenge 해결                 │
│  - cf_clearance 쿠키 발급                                       │
│  - svc.pod 내부 또는 standalone 컨테이너                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 모노레포 디렉토리 구조

```
/opt/workspace/ebooklib/
├── README.md                        # 사용자용 간략 가이드
├── vercel.json                      # Vercel 빌드/라우팅 설정
├── docs/                            # ← 이 문서들이 있는 곳
│   ├── 00-ARCHITECTURE.md           # 시스템 전체 (현재 문서)
│   ├── 01-DATA-PIPELINE.md          # 데이터 흐름
│   ├── 02-BOT-BYPASS.md             # 봇 탐지 우회 전략
│   ├── 03-EPUB-GENERATION.md        # EPUB 생성
│   ├── 04-API-REFERENCE.md          # REST API 명세
│   ├── 05-DEPLOYMENT.md             # 배포 가이드
│   └── 06-MAINTENANCE.md            # 유지보수 작업
│
├── apps/
│   ├── backend/                     # FastAPI Python 서버
│   │   ├── main.py                  # 엔트리포인트 (라우터 등록)
│   │   ├── vercel.json → 없음 (루트 사용)
│   │   ├── requirements.txt
│   │   ├── .env                     # 환경변수 (CORS_ORIGINS 등)
│   │   ├── routers/                 # API 엔드포인트 정의
│   │   │   ├── novels.py
│   │   │   ├── chapters.py
│   │   │   └── metadata.py
│   │   ├── services/                # 비즈니스 로직
│   │   │   ├── data.py              # JSON 파일 읽기
│   │   │   ├── epub.py              # EPUB 생성 (GoNoto 폰트 임베드)
│   │   │   ├── bookto31.py          # 북토끼 크롤러
│   │   │   ├── toki31.py            # 뉴토끼 크롤러
│   │   │   └── metadata.py          # 메타데이터 검색
│   │   └── lib/
│   │       └── rate_limiter.py      # 8분 + ±2분 jitter rate limiter
│   │
│   ├── frontend/                    # Next.js 16 + React 19
│   │   ├── app/
│   │   │   ├── page.tsx             # 라이브러리 메인
│   │   │   └── novel/
│   │   │       ├── [id]/page.tsx           # 소설 상세 + 회차 목록 + EPUB 다운로드
│   │   │       └── [id]/chapter/[wr_id]/page.tsx  # 챕터 읽기
│   │   ├── lib/
│   │   │   └── api.ts               # fetch 래퍼
│   │   ├── AGENTS.md / CLAUDE.md    # AI 에이전트용 가이드
│   │   ├── next.config.ts
│   │   └── package.json
│   │
├── scripts/
│   ├── json_to_epub.py              # 독립 실행 EPUB 변환기 (레거시)
│   └── ebook_watcher/               # 자동 수집 워치독 (큐 기반)
│       ├── watchdog.py              #   15분마다 큐 체크 + 워커 트리거
│       ├── ebook_worker.py          #   북토끼 챕터 자동 수집 (rate_limit 내장)
│       └── ebook_queue.py           #   CLI 큐 관리 (add/list/remove/status)
```

---

## 주요 모듈 의존성

```
┌────────────────────┐
│ apps/frontend/     │
│   app/novel/[id]/  │
│     page.tsx       │
└─────────┬──────────┘
          │ GET /api/novels/{id}/epub
          ▼
┌────────────────────┐
│ routers/novels.py  │ ← FastAPI 라우터
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐    ┌─────────────┐
│ services/epub.py   │◄───┤ services/   │
│ - build_epub()     │    │   data.py   │
│ - GoNoto 폰트 임베드│    │ - JSON 읽기 │
└─────────┬──────────┘    └──────┬──────┘
          │                       │
          ▼                       ▼
┌──────────────────────────────────────────┐
│ /opt/ai_data/flaresolverr/novels/{소설}/   │
│   - meta.json + {wr_id}.json (557 챕터)   │
└──────────────────────────────────────────┘
```

```
┌────────────────────┐
│ services/bookto31  │
│ .py - 북토끼 크롤러 │
└─────────┬──────────┘
          │ HTTP POST /v1
          ▼
┌────────────────────┐
│ FlareSolverr (127  │ ← 헤드리스 브라우저
│ .0.0.1:8191)       │    (Playwright + Chromium)
└─────────┬──────────┘
          │ 자동화된 브라우저 요청
          ▼
┌────────────────────┐
│ bookto31.com       │ ← Cloudflare Turnstile
│ (북토끼)            │
└────────────────────┘
```

---

## 데이터 라이프사이클

### 1. 수집 단계

**3가지 수집 방법** (우선순위 순):

1. **자동화 (ebook-watcher)** - 큐에 추가된 챕터를 시스템이 자동 수집
 - 위치: `/opt/workspace/ebooklib/scripts/ebook_watcher/`
 - 큐: `/opt/ai_data/flaresolverr/ebook_watcher/queue.json`
 - 트리거: `ebook-watcher.timer` @ `*:0/15` (15분마다)
 - 안전장치: 챕터 간 5분 지연, 3회 재시도, 5회 실패 시 큐 제거
 - 자세한 내용: [07-AUTOMATION.md](07-AUTOMATION.md)

2. **API 직접 다운로드** - miniebook.vercel.app API (가장 안정, 봇 탐지 없음)
 - 챕터 메타: `GET /api/novels/{id}/chapters?page=N&limit=100`
 - 챕터 본문: `GET /api/chapters/{wr_id}`

3. **북토끼 직접 크롤링** - FlareSolverr 우회 필요 (rate_limit=True 자동)
 - 챕터 목록: `services.bookto31.fetch_novel_index(wr_id)`
 - 챕터 본문: `services.bookto31.fetch_chapter(wr_id)`
 - 본문 파싱: `services.bookto31.parse_chapter_body(html)`

**권장 워크플로우**:
- 운영자는 `ebook_queue.py add`로 챕터 추가
- 시스템이 자동으로 처리 (15분 이내)
- 실패 시 워치독이 자동 재시작

### 2. 저장 단계
- 챕터 JSON 파일을 `/opt/ai_data/flaresolverr/novels/{소설명}/`에 저장
- `data.py`가 glob으로 파일을 읽어서 API 응답으로 제공

### 3. 읽기 단계
- Next.js 페이지가 `/api/novels/{id}/chapters` 호출 → 회차 목록
- 회차 클릭 → `/api/chapters/{wr_id}` 호출 → 본문 표시

### 4. EPUB 단계
- 사용자가 "EPUB 다운로드" 클릭 → `/api/novels/{id}/epub`
- `epub.py`가 DB의 모든 챕터를 모아서 EPUB 바이트 생성 (11MB+)
- GoNotoCurrent 폰트 임베드 (한글 깨짐 방지)

---

## 기술 스택

### 백엔드
- **Python 3.9**
- **FastAPI** - REST API 프레임워크
- **Pydantic** - 데이터 검증
- **ebooklib** - EPUB 생성
- **lxml** - HTML/XML 파싱 (ebooklib 의존성)
- **requests** - HTTP 클라이언트

### 프론트엔드
- **Next.js 16** - React 풀스택 프레임워크
- **React 19** - UI 라이브러리
- **TypeScript** - 타입 안전성
- **Tailwind CSS** - 스타일링

### 인프라
- **Vercel** - 호스팅 (CDN + Serverless Functions)
- **Podman** - 컨테이너 (FlareSolverr)
- **Podman Quadlet** - systemd 통합
- **Caddy** - 리버스 프록시 (포트 80/443)

### 외부 의존성
- **FlareSolverr** - 헤드리스 브라우저 (ghcr.io/flaresolverr/flaresolverr:latest) - **북토끼 우회**
- **Cloudflare** - WAF / CDN / Turnstile
- **북토끼** (bookto31.com) - **챕터 본문** SSOT
- **문피아** (munpia.com) - **메타데이터** SSOT (Brave Search로 URL 검색)
- **조아라** (joara.com) - **메타데이터** SSOT (보조)
- **namu.wiki** - **표지 이미지** 백업
- **Neon** (PostgreSQL 18.6) - Vercel 측 직접 조회 DB
- **4개 한글 폰트** - NotoSansKR (제목), RIDIBatang (본문), MaruBuri (인용), Literata (영문)

---

## 자동화 계층 (3중 보호)

```
Layer 3: ebook-watcher.timer @ *:0/15     ← 15분마다 워커 트리거
Layer 2: ebook-watcher.service            ← Restart=always (systemd)
Layer 1: devforge-watchdog @ 60초마다     ← SERVICE_TARGETS 등록
Layer 0: systemd Restart=always           ← 워치독도 자동 재시작
```

자세한 내용: [07-AUTOMATION.md](07-AUTOMATION.md)

---

## 다음 문서
- [01-DATA-PIPELINE.md](01-DATA-PIPELINE.md) - 데이터 흐름
- [02-BOT-BYPASS.md](02-BOT-BYPASS.md) - 봇 탐지 우회
- [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) - EPUB 생성
- [04-API-REFERENCE.md](04-API-REFERENCE.md) - API 명세
- [05-DEPLOYMENT.md](05-DEPLOYMENT.md) - 배포
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 유지보수
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템 (ebook-watcher + watchdog)