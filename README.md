# eBook Library - Monorepo

웹소설 수집/변환/읽기 통합 프로젝트 (Monorepo 구조)

## 구조

```
/opt/workspace/ebooklib/
├── apps/
│   ├── frontend/          # Next.js 16 + React 19 (Vercel 배포)
│   │   ├── src/
│   │   │   ├── app/       # App Router 페이지
│   │   │   │   ├── page.tsx           # 라이브러리 메인
│   │   │   │   └── novel/[id]/        # 소설 상세 + 회차
│   │   │   └── lib/api.ts             # API 클라이언트 (상대 경로)
│   │   ├── package.json
│   │   ├── vercel.json (제거됨 - 루트에서 관리)
│   │   └── .env.local
│   │
│   └── backend/           # FastAPI (Vercel Python Functions)
│       ├── main.py        # 앱 엔트리포인트 + 라우터 등록
│       ├── routers/
│       │   ├── metadata.py   # /api/metadata/lookup, /api/metadata/search
│       │   ├── novels.py     # /api/novels, /api/novels/{id}
│       │   └── chapters.py   # /api/novels/{id}/chapters, /api/chapters/{wr_id}
│       ├── services/
│       │   ├── metadata.py   # ISBNLib + Brave/DuckDuckGo 메타데이터 조회
│       │   └── data.py       # JSON 파일 읽기 서비스
│       ├── requirements.txt
│       ├── .env
│       └── vercel.json (제거됨 - 루트에서 관리)
│
├── scripts/
│   ├── json_to_epub.py    # JSON → EPUB 변환기 (독립 실행)
│   └── output/            # 생성된 EPUB 파일들
│
├── vercel.json            # Monorepo 빌드/라우팅 설정
├── .gitignore
└── README.md
```

## 아키텍처

### Monorepo 장점
- **단일 배포**: `vercel deploy` 한 번으로 프론트+백엔드 동시 배포
- **CORS 불필요**: 같은 도메인(`/api/*` 라우팅)
- **공통 환경변수**: 루트 `vercel.json`에서 관리
- **원자적 배포**: 프론트/백엔드 버전 동기화 보장

### 라우팅 (vercel.json)
| 경로 | 대상 |
|------|------|
| `/api/*` | `apps/backend/main.py` (FastAPI) |
| `/*` | `apps/frontend/.next` (Next.js) |

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/health` | 헬스체크 |
| GET | `/api/novels` | 소설 목록 |
| GET | `/api/novels/{novel_id}` | 소설 상세 |
| GET | `/api/novels/{novel_id}/chapters` | 회차 목록 (페이지네이션) |
| GET | `/api/chapters/{wr_id}` | 회차 상세 (본문 포함) |
| GET | `/api/metadata/lookup` | 단일 메타데이터 조회 |
| GET | `/api/metadata/search` | 다중 메타데이터 검색 |

### 메타데이터 서비스 파라미터
- `service`: `goob` (Google Books), `openl` (OpenLibrary), `brave` (Brave/DuckDuckGo)
- 한국어 웹소설은 `brave` 권장

## 실행 방법

### 로컬 개발 (분리 실행 권장)

```bash
# 터미널 1: 백엔드
cd apps/backend
uvicorn main:app --reload --port 8000

# 터미널 2: 프론트엔드
cd apps/frontend
npm run dev  # http://localhost:3000
```

**로컬에서만** `apps/frontend/.env.local`에 주석 해제:
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 프로덕션 (Vercel Monorepo)
```bash
# 루트에서 배포
cd /opt/workspace/ebooklib
vercel --prod
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
# 프로덕션: 비워둠 (상대 경로 사용)
# 로컬 개발 시에만:
# NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 배포 체크리스트

- [ ] Vercel 프로젝트 생성 시 **Root Directory: `/`** (루트)
- [ ] Framework: `Next.js` (자동 감지)
- [ ] Build Command: `npm run build --prefix apps/frontend && pip install -r apps/backend/requirements.txt`
- [ ] Output Directory: `apps/frontend/.next`
- [ ] Functions: `apps/backend/main.py` (maxDuration: 30s)

## 의존성

### Backend (Python 3.9+)
- fastapi, uvicorn, pydantic
- python-dotenv
- isbnlib>=3.10,<3.12 (Python 3.9 호환)
- tenacity, requests

### Frontend
- next.js 16, react 19
- typescript, tailwindcss
