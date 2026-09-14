# mini home — 문서

웹소설 리더 전용 서비스 문서

## 빠른 참조

```
minihome = Next.js (Vercel CDN) → /api/* catch-all → devforge FastAPI → JSON 파일 DB
```

### 핵심 파일
- `apps/frontend/app/page.tsx` — 메인 페이지 (소설 하이라이트 + 카테고리)
- `apps/frontend/app/library/page.tsx` — 소설 목록 (ISR)
- `apps/frontend/app/novel/[id]/` — 소설 상세 + 회차 읽기 (ISR)
- `apps/backend/main.py` — FastAPI 엔트리포인트
- `scripts/pipeline.py` — 파이프라인 (수집/저장/인덱싱)

### 핵심 데이터 위치
- `/opt/ai_data/flaresolverr/novels/{소설ID}/` — 챕터 JSON + 인덱스 캐시
- `/opt/ai_data/flaresolverr/epub/` — EPUB 캐시

### 외부 의존성
- **FlareSolverr** — Cloudflare 우회 (북토끼)
- **Playwright + DataImpulse** — 뉴토끼 우회
- **namu.wiki** — 메타데이터 + 표지
- **Vercel** — ISR 호스팅

## 주의: ebooklib과의 관계

minihome은 ebooklib의 코드를 **심볼릭 링크로 참조**합니다:
- **공유** (symlink): pages, components, API routes, backend routers/services/lib, scripts, docs
- **minihome 고유** (실제 파일): `app/page.tsx` (새 메인 페이지), `app/layout.tsx` (다른 nav), `lib/server.ts` (portal 타입 제거), `backend/main.py` (pipeline 미임포트)

변경 사항이 생기면 ebooklib의 해당 파일을 직접 수정하고, symlink를 통해 minihome에 자동 반영됩니다.
