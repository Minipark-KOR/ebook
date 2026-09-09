# eBook Library - Monorepo

웹소설 수집/변환/읽기 통합 프로젝트 (Monorepo 구조)

> 📚 **상세 문서**: [`docs/`](docs/README.md) 디렉토리에 시스템 전체 설명이 있습니다.

## 핵심 요약
- 북토끼(bookto31.com) 등 Cloudflare 보호 사이트의 한국 웹소설 챕터를 자동 수집
- **자동화 워치독** (ebook-watcher): 큐에 추가하면 15분마다 자동 수집, devforge-watchdog이 죽으면 자동 복구
- 로컬 JSON DB에 저장 (`/opt/ai_data/flaresolverr/novels/`)
- FastAPI 백엔드 + Next.js 프론트엔드를 Vercel Monorepo로 단일 배포
- EPUB 다운로드 지원 (한글 폰트 임베드, 어디서나 읽기 가능)
- 봇 차단 우회: FlareSolverr (북토끼), curl_cffi (뉴토끼), rate limiter (8분 + ±2분)

## 챕터 자동 수집 (간단 사용법)

```bash
# 작품 메인 wr_id로 discover → 큐 등록 → collect
python3 scripts/pipeline.py all 25575 "오늘만 사는 기사"

# 상시 루프 (연재작 자동 수집 + 월 1회 discover)
python3 scripts/pipeline.py loop --source bookto31

# 또는 Admin 페이지 (https://miniebook.vercel.app/admin)에서 URL 입력
```

- 파이프라인 5단계: discover → collect → enrich → index → revalidate
- 북토끼 5~10분 챕터 간 안전 지연 + 3회 재시도, 실패 시 DLQ(failed.json) 기록
- devforge-watchdog + systemd가 60초/10분 이중 감시

## 구조

```
/opt/workspace/ebooklib/
├── apps/
│   ├── frontend/          # Next.js 16 + React 19 (Vercel 배포)
│   │   ├── app/           # App Router 페이지
│   │   │   ├── page.tsx                   # 라이브러리 메인 (ISR)
│   │   │   ├── admin/page.tsx             # 파이프라인 관리
│   │   │   ├── novel/[id]/                # 소설 상세 + 회차
│   │   │   │   └── chapter/[wr_id]/       # 회차 읽기
│   │   │   └── api/[...slug]/route.ts     # catch-all 프록시 → devforge
│   │   ├── lib/api.ts             # 타입 + fetch 래퍼
│   │   ├── next.config.ts
│   │   └── package.json
│   │
│   └── backend/           # FastAPI (devforge, OCI)
│       ├── main.py        # 앱 엔트리포인트 + 라우터 등록
│       ├── routers/
│       │   ├── metadata.py   # /api/metadata/lookup, /api/metadata/search
│       │   ├── novels.py     # /api/novels, /api/novels/{id}
│       │   ├── chapters.py   # /api/novels/{id}/chapters, /api/chapters/{wr_id}
│       │   └── pipeline.py   # 파이프라인 시작/상태 (Admin용)
│       ├── services/
│       │   ├── data.py       # JSON 파일 읽기 (인덱스 캐시)
│       │   ├── epub.py       # EPUB 생성 (한글 4폰트)
│       │   ├── bookto31.py   # 북토끼 크롤러 (FlareSolverrSession)
│       │   ├── metadata.py   # 메타데이터 검색
│       │   └── metadata_namu.py # namu.wiki 메타데이터
│       ├── lib/              # 공통 레이어
│       │   ├── flaresolverr_client.py # FlareSolverr 세션 관리
│       │   ├── storage.py             # 챕터 저장/메타 관리
│       │   ├── toki31_playwright.py   # 뉴토끼 Playwright 추출기
│       │   └── rate_limiter.py        # SQLite rate limiter
│       └── requirements.txt
│
├── scripts/
│   ├── pipeline.py                  # 파이프라인 (discover/collect/enrich/index/revalidate/loop)
│   ├── json_to_epub.py              # JSON → EPUB 변환기
│   └── fonts/                       # EPUB 한글 폰트
│
└── README.md
```

## 아키텍처

### 배포 구조
- **프론트엔드**: Vercel (Next.js ISR)
- **백엔드**: devforge (Oracle Cloud) — FastAPI @ 8089
- **Vercel `/api/*`** → catch-all 프록시 → devforge 백엔드
- 데이터는 devforge 로컬 `/opt/ai_data/flaresolverr/novels/` JSON 파일 단일 소스

### 라우팅
| 경로 | 대상 |
|------|------|
| `/` , `/novel/*` | Next.js (ISR, devforge 직접 fetch) |
| `/api/*` | `app/api/[...slug]/route.ts` → devforge 백엔드 프록시 |

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/novels` | 소설 목록 |
| GET | `/api/novels/{novel_id}` | 소설 상세 |
| GET | `/api/novels/{novel_id}/chapters` | 회차 목록 (페이지네이션) |
| GET | `/api/chapters/{wr_id}` | 회차 상세 (본문 포함, prev/next) |
| GET | `/api/metadata/lookup` | 단일 메타데이터 조회 |
| GET | `/api/metadata/search` | 다중 메타데이터 검색 |
| GET | `/api/novels/{novel_id}/epub` | EPUB 다운로드 |

### 메타데이터 서비스 파라미터
- `service`: `goob` (Google Books), `openl` (OpenLibrary), `brave` (Brave/DuckDuckGo)
- 한국어 웹소설은 `brave` 권장

## 실행 방법

### 로컬 개발 (분리 실행 권장)

```bash
# 터미널 1: 백엔드
cd apps/backend
venv/bin/python -m uvicorn main:app --reload --port 8089

# 터미널 2: 프론트엔드
cd apps/frontend
npm run dev  # http://localhost:3000
```

**로컬에서만** `apps/frontend/.env.local`에 주석 해제:
```env
NEXT_PUBLIC_API_URL=http://127.0.0.1:8089
```

### 프로덕션 (Vercel + devforge)
```bash
# 프론트엔드 배포 (Vercel, Root Directory=apps/frontend)
cd /opt/workspace/ebooklib
vercel deploy --prod --project miniebook

# 백엔드는 devforge에서 실행 중 (port 8089) — 재시작 필요 시:
#   cd apps/backend && setsid nohup venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8089 &
```

### EPUB 변환 (독립 실행)
```bash
cd /opt/workspace/ebooklib
python scripts/json_to_epub.py --help
python scripts/json_to_epub.py --start 1 --end 10 --output my_novel.epub
python scripts/json_to_epub.py  # 전체 변환
```

## 데이터 소스

소설 JSON 파일: `/opt/ai_data/flaresolverr/novels/{소설명}/*.json`

```json
{
  "wr_id": 21431,
  "chapter": 1,
  "title": "하남자의 탑 공략법 - 1화",
  "content": "본문 내용...",
  "content_length": 5804
}
```

## 환경 변수

### 백엔드 (`apps/backend/.env`)
```env
ENV=development
DEBUG=true
CORS_ORIGINS=["https://miniebook.vercel.app"]  # Vercel에서 자동 처리됨
# BRAVE_API_KEY=your_key  # 선택사항
```

### 프론트엔드 (`apps/frontend/.env.local`)
```env
# 로컬 개발 시:
# NEXT_PUBLIC_API_URL=http://127.0.0.1:8089
# 프로덕션: Vercel 대시보드에서 NEXT_PUBLIC_API_URL 설정
```

## 배포 체크리스트

- [ ] Vercel 프로젝트 생성 시 **Root Directory: `apps/frontend`**
- [ ] Framework: `Next.js` (자동 감지)
- [ ] Build Command: `npm run build`
- [ ] Output Directory: `.next`
- [ ] 환경변수: `NEXT_PUBLIC_API_URL=https://devforge.152-69-229-246.nip.io`, `VERCEL_REVALIDATE_TOKEN`
- [ ] 백엔드는 devforge에서 uvicorn port 8089 실행 중 (Caddy nip.io 노출)

## 의존성

### Backend (Python 3.9+)
- fastapi, uvicorn, pydantic
- python-dotenv
- isbnlib>=3.10,<3.12 (Python 3.9 호환)
- tenacity, requests
- curl_cffi (TLS fingerprint 위장)

### Frontend
- next.js 16, react 19
- typescript, tailwindcss
