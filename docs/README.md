# ebooklib 문서

이 디렉토리는 ebooklib 시스템의 전체 문서를 포함합니다.

## 문서 목록

| 파일 | 설명 |
|------|------|
| [00-CHANGELOG.md](00-CHANGELOG.md) | 모든 변경 이력 (2026-09-05) |
| [00-ARCHITECTURE.md](00-ARCHITECTURE.md) | 시스템 전체 아키텍처 (다이어그램, 모듈, 데이터 흐름) |
| [01-DATA-PIPELINE.md](01-DATA-PIPELINE.md) | 데이터 수집 → 저장 → API 응답 흐름 |
| [02-BOT-BYPASS.md](02-BOT-BYPASS.md) | Cloudflare / CloudFront 봇 탐지 우회 전략 |
| [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) | EPUB 생성 시스템 (4개 한글 폰트 임베드) |
| [04-API-REFERENCE.md](04-API-REFERENCE.md) | REST API 엔드포인트 명세 |
| [05-DEPLOYMENT.md](05-DEPLOYMENT.md) | Vercel + FlareSolverr + 자동화 시스템 배포 |
| [06-MAINTENANCE.md](06-MAINTENANCE.md) | 운영 중 유지보수 작업 + 자동화 관리 |
| [07-AUTOMATION.md](07-AUTOMATION.md) | 자동화 시스템 (ebook-watcher + devforge-watchdog) |

## 빠른 참조

### 시스템 한 줄 요약
```
ebooklib = 한국 웹소설 크롤러 (북토끼/FlareSolverr 우회) → 로컬 JSON DB → FastAPI → Next.js → Vercel
```

### 핵심 파일
- `apps/backend/services/epub.py` - EPUB 생성 (GoNoto 폰트 임베드)
- `apps/backend/services/bookto31.py` - 북토끼 크롤러 (rate_limit 내장)
- `apps/backend/lib/rate_limiter.py` - 8분 + ±2분 jitter
- `apps/frontend/app/novel/[id]/page.tsx` - 소설 상세 + 회차 목록 + EPUB 다운로드

### 핵심 데이터 위치
- `/opt/ai_data/flaresolverr/novels/{소설ID}/` - 챕터 JSON 파일들
- `/opt/ai_data/flaresolverr/rate_limiter.db` - rate limiting 로그

### 핵심 외부 의존성
- **FlareSolverr** (127.0.0.1:8191): Cloudflare Turnstile 우회
- **북토끼** (bookto31.com): 챕터 데이터 소스
- **miniebook.vercel.app**: 자체 DB API (안전한 fallback)

## 누구를 위한 문서?
- **새로 합류한 개발자**: 00 → 01 → 04 순서로 읽기
- **운영자**: 06 (유지보수) + 02 (봇 우회)
- **AI 에이전트**: 모든 문서 (특히 00 아키텍처 + 06 유지보수)

## 기여
문서 오류/추가 제안은 GitHub 이슈 또는 PR로 알려주세요.