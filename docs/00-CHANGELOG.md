# 변경 이력 (CHANGELOG)

> ebooklib의 모든 주요 변경 사항. 최신이 위.

## 2026-09-12 (toki31 안전캡 조정)

### 회차 트래픽 상한 타이트닝 (JS/wasm 캐시 반영)
- JS/wasm 로컬 캐시로 정상 트래픽이 크게 줄었으므로 상한을 정상 대비 여유만 남기도록 조정
- 콜드 **2.5 → 1.5MB** (정상 ~0.94MB), 웜 **2.0 → 0.8MB** (정상 ~0.18MB)
- 캐시 미스로 JS 일부 재다운로드해도 웜 0.8MB 안에서 수용
- 검증: 콜드 958KB / 웜 186·184KB 모두 상한 내, 본문 정상 추출

## 2026-09-12 (toki31 JS 청크 캐시)

### JS 청크 로컬 캐시 재서빙 (웜 회차 ~0.18MB로 대폭 절감)
- **문제**: toki31이 JS 청크(~840KB)를 매 챕터 재다운로드 (anti-bot, no-store → 브라우저 캐시 불가)
- **사전 검증**: 2개 챕터 로드 → 동일 URL의 JS 내용 해시가 **전부 동일 (불안정 0/22개)** → 캐시 안전 확인
- **구현**: wasm과 동일한 패턴 — 첫 요청만 `route.fetch()`로 실다운로드 → 메모리 캐시 →
  이후 `route.fulfill`로 0네트워크 재서빙 (`_js_cache`/`_js_cache_hits`)
- **계상**: 캐시 재서빙 JS는 `_js_cache_hits`로 계상 제외 (0 네트워크)
- **효과**: 웜 회차 420~670KB → **184~186KB** (문서 165KB + API ~24KB만)
  - 3회차: 942+186+184KB = ~1.28MB → 50GB ≈ **27만 회차** 수용 (기존 대비 ~5배)
- 트래커(whoas.xyz)는 캐시 전에 차단

### 검증 (화산귀환 toki31 3챕터)
- 콜드 942KB / 웜 186KB·184KB — 본문 정상 추출 (5696/6263/5407자)

## 2026-09-12 (toki31 ad_guard 캐시)

### ad_guard_bg.wasm 로컬 캐시 재서빙 (403KB/회차 절약)
- **문제**: toki31의 anti-adblock `ad_guard_bg.wasm`(403KB)이 매 챕터 재다운로드.
  차단하면 본문 추출이 실패(콘텐츠에 필수) → 차단 불가
- **해법 (웹 조사: Playwright route.fulfill WASM 모킹 패턴)**: 차단 대신
  **첫 요청만 실다운로드 → 메모리 캐시 → 이후 요청은 `route.fulfill`로 재서빙**
  - JS에는 정상 wasm 응답으로 보여 anti-adblock 탐지도 트리거 안 함
  - 캐시된 wasm은 0 네트워크 → 웜 회차 ~400KB 절약 (1.0MB → 0.42~0.67MB)
- **트래커 차단**: `whoas.xyz` 등 트래커/광고 도메인 `route.abort()`
- **계상**: 캐시 재서빙 wasm은 `route.fulfill` 응답(requestStart=-1)이라 timing 판정 불가
  → URL 마커(`ad_guard_bg.wasm`)로 직접 계상 제외 (첫 1회 실다운로드도 제외 — 무시 가능 수준)
- **버그 수정**: `route.fulfill(contentType=...)` → `content_type` (Playwright Python snake_case)

### 검증 (화산귀환 toki31 3챕터)
- 콜드 666KB / 웜 422KB·673KB — 모두 본문 정상 추출 (5696/6263/5407자)
- ad_guard_bg.wasm 캐시 히트로 웜 회차 ~400KB 절약 확인

## 2026-09-12 (toki31 검증 보강)

### 캐시 히트 계상 제외 보정
- `_on_response` 캐시 판정 엣지 케이스 수정: timing 키가 없거나 음수(-1)면
  카운트하도록 보정 (기본값 0으로 0<1 오판 → 과소계상 방지)
- 판정 시뮬레이션 7케이스 전부 정확 (캐시 히트/네트워크/빈 timing/음수/키 누락/None)

### 양방향 검증 결과
- **정방향(설정→동작)**: `.env.local` → `_resolve_proxy` DataImpulse+`__cr.kr` 선택,
  상한(콜드2.5/웜2.0MB), `_source_chapter_url` → 23.ondobook.net/toki31.com
- **역방향(동작→설정)**: toki31 수집 3챕터(1807/1342/1345)의
  chapter/source/url/media_type 저장 일관성 확인
- **데이터 무결성**: 화산귀환 인덱스 재구축(684개, 신규 3챕터 포함),
  전 소설 check-dupes 중복 0/불일치 0, missing.json = 바바리안 911만
- **런타임**: 루프 오류 없음, ondobook 백필 정상 진행(ch~1261),
  도메인 헬스 3소스 모두 ok
- `.env.local` 8개 필수 키 전부 유효 (프록시 자격증명)

## 2026-09-12 (toki31 백필 테스트)

### DataImpulse 50GB 충전 → 3회차 백필 테스트 (데이터 절약 검증)
- **프록시 우선순위 전환**: `("maskproxy","dataimpulse")` → `("dataimpulse","maskproxy")`
  - DataImpulse 주력(충전분) + MaskProxy 폴백. DataImpulse는 `__cr.kr`(한국 IP 회전) 접미어 필요
  - MaskProxy 자격증명 갱신 (.env.local — 시크릿)
- **트래픽 상한 조정**: 사이트가 JS/wasm을 매 챕터 재다운로드(anti-bot) → 콜드 ~1.3MB/웜 ~1.6MB 정상
  - 콜드 1.5→**2.5MB**, 웜 1.0→**2.0MB** (정상 챕터가 안전장치에 차단되지 않도록)
- **트래픽 실측 개선**: 캐시 히트 응답(`responseStart-requestStart < 1ms`)은 집계 제외
  - 안 하면 브라우저 재사용 시 JS 재다운로드를 과대계상해 웜 상한 오차단
- **테스트 결과 (화산귀환 3개 누락 챕터)**:
  - ch1807: 성공 5696자 / ch1342: 성공 6263자 / ch1345: 성공 5407자
  - **평균 0.92MB/회차** → 50GB ≈ 5만 회차 수용 (전체 백필 ~2300회차 = ~3.5GB)
  - **주의**: 1342/1345는 ondobook에 본문이 없었지만 toki31에는 정상 존재 → toki31 폴백의 가치 확인
- **확인된 데이터 절약**: 리소스 차단(media/CSS/이미지) + 브라우저 재사용(웜 3s) 정상 작동
  - ad_guard_bg.wasm(403KB/챕터)은 콘텐츠 추출에 필수 → 차단 불가

## 2026-09-12 (누락 처리)

### 빠진 화수(누락 챕터) 추적/재처리 시스템
- **`pipeline.py check-gaps`**: 소설별 빠진 화수 감지 → `missing.json` 기록
  - 감지 유형: `gap`(저장/큐 모두 없는 누락), `empty_source`(사이트에 본문 없는 빈 챕터)
  - 월간 사이클에 자동 포함 (discover 후 추적 갱신)
- **`pipeline.py retry-missing`**: missing.json의 누락/빈 챕터를 소스에서 **재발견 → 정확한 wr_id로 재큐**
  - `--dry-run`으로 미리보기, gnuboard(bookto31 계열) 소스만 자동 지원
- **현황**: 화산귀환 누락 1807(gap)·1342/1345(빈 챕터), 바바리안 911(gap)
  - 1342/1345는 ondobook에 본문 없음 → 재시도해도 빈 챕터 DLQ 반복, **toki31 전환 필요**
  - 1807/911도 현재 소스(ondobook)에 없음 → **toki31(유료 프록시) 폴백이 유일한 수집 경로**

## 2026-09-12 (검증 보강)

### 버그 수정 (양방향 검증 과정에서 발견)
- **빈 챕터로 백필 정체**: 사이트에 본문이 없는 챕터(예: ondobook 1345화=wr_id 637395)를
  FlareSolverr rate limit(8분)×3회×3사이클로 재시도 → ~72분 정체
  → **빈 본문(사이트에 내용 없음)은 재시도 생략 + 1회 실패로 즉시 DLQ** (백필 정체 방지)
- **저장 url 하드코딩**: `save_chapter`가 url을 `https://{source}.com/...`로 고정 → 도메인 변경 후
  저장 url이 죽은 주소 가리킴 → **`get_base_url(source)` 기반으로 수정** (`_source_chapter_url`)
- **챕터 번호 오탐**: `_extract_chapter_num`의 MULTILINE 폴백이 본문 중간 "N화" 언급을 잡아
  check-dupes 오탐 + 저장 보정 오작동 위험 → **엄격한 첫 줄 추출(`_claimed_chapter_strict`) 도입**
- **domain_health 덮어쓰기**: collect의 status 기록이 도메인 헬스 결과를 지움
  → **`_write_status`가 domain_health 기존 값 보존** (모니터링 지속)

### 양방향 검증 결과 (정방향/역방향)
- 정방향(설정→동작): sources.json → `load_sources`/`get_base_url`/`candidate_bases`/URL매칭 전부 통과
- 역방향(동작→설정): 저장 챕터 url이 설정 base_url과 일치 (ondobook 수집분 23.ondobook.net)
- 전 소설 check-dupes: 중복 0, 챕터 번호 불일치 0
- 도메인 헬스: bookto31/toki31/newto31 모두 `ok` (FlareSolverr 복구 반영)
- 백필(화산귀환 0~1344화): 빈 챕터 1345만 DLQ, 나머지 정상 진행

## 2026-09-12 (최신)

### 북토끼 도메인 변경: bookto31.com → 23.ondobook.net
- **북토끼가 23.ondobook.net으로 주소 변경** → `sources.json`/`lib/sources.py`의 `base_url`·`domains` 갱신
- **주의**: ondobook은 bookto31.com과 **DB/wr_id 체계가 다름** (wr_id가 재매핑됨)
  - 예: bookto31의 화산귀환 main_wr_id=12000 → ondobook에서 다른 소설. ondobook 화산귀환 = wr_id=4419
  - 기존 소설의 `meta.main_wr_id`는 discover 실행 시 자동 갱신됨 (소스별 wr_id 재발견)
- **ondobook 본문 파서 보완**: ondobook 본문 div가 `view-content` 단독 클래스
  → `_extract_book_text_viewer`가 `view-content book-text-viewer` + `view-content` 모두 지원

### 도메인 자동 전환/감지 시스템 (`lib/domain_router.py` 신규)
- **리다이렉트 최종 URL 감지**: 요청 후 응답 최종 URL의 호스트가 다르면 `sources.json`의 `base_url` 자동 갱신
  - FlareSolverr `sol.url`, toki31 Playwright `page.url`, discover의 `pg.url` 모두 연동
  - 리다이렉트가 사라져도 새 주소로 계속 동작
- **미러 도메인 페일오버**: `candidate_bases()`가 base_url → domains 목록 순회, 현재 도메인 사망 시 자동 전환
- **이전 URL 폐기**: `update_base_url(discard_old=True)` — 도메인이 넘어가면 이전 URL은 `domains`에서 제거(새 호스트만 유지)
- **도메인 헬스체크** (loop 30분 간격):
  - bookto31: FlareSolverr 홈 실응답 / toki31: DNS 확인 (유료 트래픽 절약)
  - 사망 → 자동 페일오버, 결과는 `status.json#domain_health`·`domain_status.json`에 기록
  - **FlareSolverr 자체 다운 시 "unknown" 판정** (사이트 다운 오판 방지)
- `sources.json` 갱신은 `.bak` 백업 후 원자적 쓰기

### 중복 본문 감지/처리 (재다운로드/중복 저장 방지 강화)
- **동일 본문 해시 검출** (collect 저장 전): 같은 본문이 다른 화수(chapter 번호)로 저장돼 있으면
  → **저장 생략 + `duplicates.json` 기록 + 큐에서 제거** (더처)
- **챕터 번호 오프바이원 자동 보정**: 본문 표기("N화")가 저장 chapter와 다르고 충돌 없으면
  저장 단계에서 본문 표기 우선 (소스 wr_id→화수 매핑 오류 대응)
- **`pipeline.py check-dupes [소설] [--fix]`**: 기존 데이터 감지 + 수리
  - 감지: 동일 본문이 다른 화수로 저장된 중복, 본문 표기와 저장 chapter 불일치
  - `--fix`: 챕터 재정렬 + 중복 제거, 변경/삭제 파일 `_dupe_backup_*`로 백업, 인덱스 재구축

### 화산귀환 데이터 수리 (오프바이원 115건 + 중복 1건)
- **원인**: bookto31의 `wr_id N`이 실제 **(N+1)화** 본문 반환 (discover 매핑 오프바이원)
- **수리**: 13807~13921(115건) 챕터 번호를 본문 표기 기준으로 재정렬 (13807→1808 … 13920→1921)
  - `13921.json`(ch1921로 표기, 실제 1922화) = `12000.json`(ch1922)과 동일 본문 → 중복 제거(백업)
- **결과**: 1808~1922 연속. 단 **1807화는 원래 누락**이었음이 드러남
  (toki31 1806까지, bookto31 1808부터 — 현 소스로 재수집 불가)

### FlareSolverr(svc pod) 포트 포워딩 이슈
- **증상**: 컨테이너는 running인데 `127.0.0.1:8191`(및 8000/8002/8085) 호스트 접근 불가
  → svc pod의 pasta 포트포워딩 프로세스 소실 (netns 재생성 문제)
- **해결**: `podman pod restart svc` (또는 `systemctl --user restart svc-pod container-flaresolverr`)로 복구
- **영향**: 복구 전까지 bookto31 크롤링 불가 + devforge-mcp(8000) 접근 불가

## 2026-09-10

### bookto31 본문 파싱 강화
- **`parse_chapter_body`**: 단순 `.*?</div>` → **중첩 div 안전 추출** (`_extract_book_text_viewer`)
  - 본문 div가 중첩 div를 포함하면 기존엔 첫 `</div>`에서 잘렸음 → depth 추적 파서로 수정
- **불필요 요소 제거 강화**: script/style/iframe/noscript/form + 광고(banner/ad/popup 등) div 제거
- **엔티티 디코딩**: `&amp;`/`&lt;` 등 → 실제 문자
- 본문은 원래 텍스트만 저장 중이었고, 잔여물로 보이던 `<탑 26층...>` 등은 소설 본문 자체임을 확인

### 소스별 처리 격리 (북토끼/뉴토끼 독립 동작)
- **문제**: 단일 큐 + `run_collect(limit=1)` 전체 처리 → toki31(유료 프록시) 407/한도 도달 시
  큐가 toki31로 가득 차면 bookto31이 굶어 죽음
- **`sources.py`/`sources.json`**: `traffic_limited` 필드 추가
  - bookto31 = false (FlareSolverr 로컬, 무료)
  - toki31 = true (DataImpulse/MaskProxy, 유료)
- **`_run_collect_locked`**: 트래픽 가드를 `traffic_limited` 소스에만 적용
  - bookto31은 한도와 무관하게 계속 처리, toki31만 자정까지 대기
- **`loop`**: `list_sources()` 순회하며 소스별 `run_collect(limit=1, source_filter=src)` 호출
  - 유료 소스가 한도 도달해도 무료 소스는 계속
  - 유료 전부 도달 시에만 자정 대기 (최대 300초 단위)

### 트래픽 절약 + 안전장치 (DataImpulse 소진 대응)
- **문제**: toki31 수집이 회차마다 Chromium 새로 실행 + 이미지/폰트/CSS 전부 다운로드
  → 회차당 수 MB × 큐 2,031건 = DataImpulse 5GB 순식간 소진
- **`lib/toki31_playwright.py` 리팩터**:
  - `Toki31Collector` 클래스 — 브라우저/컨텍스트/페이지 프로세스 수명 동안 **재사용**
    (JS 번들·쿠키 재사용 → 회차당 ~430KB, 옛 collector 패턴 복원)
  - `page.route()`로 **image/font/media/stylesheet 차단** (`route.abort()`)
  - **프록시 우선순위 전환**: MaskProxy($0.87/GB) → DataImpulse($1/GB) (설계 문서대로)
  - 응답 바이트 실측 누적 (`get_traffic_total_bytes()`)
  - 프록시 인증 연속 실패 시 브라우저 리셋
- **`lib/traffic_guard.py` 신규**: 일일 트래픽 한도 가드
  - 다운로드 바이트 실측 누적 (state 파일: `traffic_state.json`)
  - 일일 한도 `EBOOK_DAILY_TRAFFIC_LIMIT_MB` (기본 200MB) 초과 시 수집 일시정지
  - 날짜 변경 시 자동 리셋 → 다음 날 자정에 자동 재개
  - `pipeline.py traffic` 상태 조회 명령
- **회차 단위 안전장치** (`toki31_playwright.py`):
  - `novel-content` 페이로드 **60KB 초과 시 차단** (정상 ~24KB의 2.5배) — `TOKI31_CONTENT_MAX_KB`
  - 회차별 총 트래픽 상한: **콜드(첫 로드) 1.5MB / 웜 1MB** — `TOKI31_CHAPTER_COLD_MAX_MB` / `TOKI31_CHAPTER_WARM_MAX_MB`
  - 첫 로드 성공 시 `_is_cold=False` (JS 번들 캐시 완료), 브라우저 리셋 시 콜드 복귀
- **재다운로드 방지 (소스 무관 dedup)** (`pipeline.py`):
  - collect 전 이미 저장된 chapter 확인 → 다운로드 없이 스킵
  - bookto31(gnuboard wr_id) / toki31(episode_id)가 **같은 chapter를 서로 다른 ID로
    재발견하는 소스 간 중복**을 chapter 번호 기준으로 차단
  - 소설별 저장 chapter 캐시 + 저장 후 무효화 (`_load_saved_chapters`/`_invalidate_saved_chapters`)
  - **`_chapters_index.json` 캐시 재사용** (Scrapy RFPDupeFilter 영속화 대응 — 파일 수
    일치 시 인덱스로 빠르게 로드, 불일치 시 전체 스캔 폴백)
  - **dedup 스킵 카운트 메트릭**: collect 결과/`traffic`에 노출 (Scrapy `dupefilter/filtered` 대응)
- **`pipeline.py`**:
  - `_collect_newtoki` None 반환 시 `(False, ...)` → **TypeError 크래시 루프 해결**
  - `run_collect`: 한도 초과 시 조기 반환(`traffic_exceeded`) + 회차별 트래픽 누적
  - `loop`: 한도 초과 시 자정까지 대기 후 재개 (queue 보존)
  - `discover_toki31`: 프록시 MaskProxy 우선 + 리소스 차단

## 2026-09-09

### 다중 소스 아키텍처 버그 수정
- **toki31 수집 속도 회복**: speed_hint 30→5초 (IP 회전으로 30초는 과보수), 루프 사이클 대기 5→1초
  - ~47초/화 → **~18초/화** (전체 3,240화 ≈ 16시간)
- **`discover_toki31` 프록시 가드**: DATAIMPULSE/MASKPROXY 자격증명 없으면 조기 반환 (빈 proxy로 Playwright 실패 방지)
- **`discover_toki31` dry_run 최적화**: 제목만 추출 (전체 에피소드 페이징 제거 → `_extract_title` 빠르게)
- **ETA 과소평가 수정**: `_estimate_seconds_per_chapter` 캡이 hint×2(10초)로 타이트 → **hint×4(20초)** 로 (실측 ~18초 반영)

### 다중 소스 아키텍처 (소스 레지스트리)
- **요구사항**: 여러 소스 수용, 도메인 유동(주소 변경), 단일 소스 고정 금지
- **`sources.json`** (`apps/backend/`): 소스 정의를 코드 수정 없이 관리
  - `domains`(URL 매칭), `base_url`(크롤링 주소), `collector`(수집기), `discover`(전략), `speed_hint_sec`(ETA)
  - 북토끼가 `bookto42.com`으로 옮기면 domains/base_url만 수정 — 코드 불변
- **`lib/sources.py`**: pydantic(BaseModel) 검증 레지스트리
  - `get_source_from_url()` — URL_PATTERNS 하드코딩 대체
  - `get_base_url()`, `get_collector()`, `get_discover()`, `get_speed_hint()`
- **하드코딩 제거**: bookto31.py BASE_URL, toki31_playwright.py 도메인, run_discover URL, URL_PATTERNS → 전부 레지스트리
- **루프 다중 소스**: `loop --source toki31` 고정 해제 → **모든 소스 처리**
  - 소스별 페이싱을 run_collect 내부 적응형 딜레이로 (speed_hint 기준: toki31 5~60초, bookto31 300~600초)
  - systemd `ExecStart=pipeline.py loop` (source 필터 없음), WatchdogSec 600→1800
- **toki31 전용 discover**: `discover_toki31()` — 에피소드 목록(화수→episode_id) 기반 큐잉
  - `run_discover`가 `get_discover(source)`로 gnuboard / toki31_episodes 라우팅
- **`_auto_discover`**: 각 소설의 meta.source에 따라 소스별 discover (현재 gnuboard만, toki31은 follow-up)
- **웹/Context7 검증**: Factory+Registry 패턴(웹), pydantic-settings(JSON 설정 검증) → pydantic BaseModel 채택

### Admin ETA 소스 인지 수정 (예상 완료 시간 정확화)
- **문제**: ETA가 과거 bookto31 수집 속도(collected_at 간격 ~400초) 기준 → toki31 전환 후에도
  "3일/5일"로 표시 (오늘만·화산은 toki31 큐인데 bookto31 속도로 계산)
- **수정** (`routers/pipeline.py`):
  - `_estimate_seconds_per_chapter(novel_dir, source)`: 큐의 source 반영
    - toki31 → 측정값 상한 60초 (fallback 30초), bookto31 → 상한 3600초 (fallback 300초)
  - `get_novel_status`: **큐 순서(FIFO) 반영 ETA** — 앞 소설의 대기 회차까지 다 받아야
    다음 소설이 시작되므로 누적 계산 (예: 오늘만 = 게임+화산+오늘만 모두 받는 시간)
  - queue 1회 로드 (기존 반복 로드 제거), TTL 캐시 키에 source 포함
- **결과**: 게임 ~3시간, 화산 ~1일9시간, 오늘만 ~1일20시간 (기존 3일/5일 → 수배 단축 표시)

### toki31 일괄 수집 전환 (bookto31 → toki31)
- **배경**: bookto31은 Cloudflare rate limit(1화/5~8분) → 3,150화 ≈ 11일 소요
- **toki31 전환**: 3개 소설(게임/화산/오늘만) 에피소드 맵(화수→episode_id) 수집 후 큐 재구성
  - toki31이 화수도 더 많음: 게임 934, 화산 1,945, 오늘만 1,025 (bookto31 대비 +37/+23/+186)
  - **~15~30초/화** → 전체 ≈ **17시간** (bookto31 11일 대비 15배)
- **episode_id ≠ bookto31 wr_id**: toki31의 episode_id는 완전히 다른 체계 → 에피소드 맵으로 매핑
  - 새 파일은 `{toki31_episode_id}.json`으로 저장, 기존 bookto31 파일과 혼재 (chapter 번호 정렬로 정상)
- **루프 개선**:
  - `--source toki31` 루프 (사이클 대기 300초→5초, 내부 딜레이 스킵 → 고속)
  - `--source`가 novel_title로 잘못 들어가던 버그 수정 (위치 인자만 title)
- **의존성**: venv에 `playwright`, `cryptography` 설치 누락 → toki31 수집 즉시 실패하던 버그 수정
- **IP 회전 확인**: 매 세션 다른 한국 ISP IP (DataImpulse __cr.kr) → IP 차단 무력화, 병렬은 collect 락 직렬화로 보류

### discover 재발 방지 패치 (회차 누락 예방)
- **원인 확인**: 잘못된 main_wr_id(예: 게임=42424)로 discover하면 bookto31 페이지에
  **에피소드 셀렉트가 0개** → `extract_chapter_wr_ids_from_index` 빈 결과 → 즉시 중단, 초반부 누락
- **`run_discover`**: 전달 wr_id로 아무 회차도 못 찾으면 **저장된 챕터 wr_id로 자동 재시도**
- **`run_discover` 종료 조건**: 신규 0회가 **1회→2연속**일 때만 중단 (윈도우 1회성 겹침으로 조기 중단 방지)
- **`_auto_discover`**: main_wr_id가 없어도 **저장된 챕터에서 wr_id를 유도**해 월간 재-discover
  (이전엔 main_wr_id 없는 소설이 안전망에서 제외 → 게임 소설이 수개월 누락된 채 방치된 원인)

### 회차 수 누락 버그 수정 (discover 불완전)
- **증상**: 게임_속_바바리안이 30/240으로 표시되지만 소스(bookto31)에 1~897화가 존재
- **원인**: 초기 discover가 잘못된 main_wr_id(42424)로 실행 → epage 페이지네이션의 일부만 수집 (658~897 누락 없이 초반부 1~657 전부 누락)
- **수정**: 게임 소설 재-discover → 큐 210→867개, **1~897화 완전 확보**
- **추가 발견·수정**: 오늘만_사는_기사도 451~476(26화) 갭 → 재-discover로 1~839화 완전
  - 화산귀환(1~1922), 하남자(1~557), 아포(1~287)는 완전 확인
- **교훈**: `epage`/`spage` 페이지네이션은 소스 전체를 커버해야 함. main_wr_id 오기록 시 불완전 → 월간 `_auto_discover`가 백필 (단, main_wr_id 없는 소설은 제외되므로 주의)
- **수정 데이터**: 게임 meta main_wr_id=42500, totalChapters=897 / 오늘만 totalChapters=839

### 버그 수정: namu rate-limit 블로킹 + ETA 성능
- **namu.wiki 30분 rate limit이 수집을 블로킹하던 문제 수정**
  - `run_all`: ENRICH를 bulk collect **이후**로 이동 (이전엔 namu 대기 때문에 수집 시작이 최대 30분 지연)
  - `run_enrich_background()`: namu 메타 갱신을 백그라운드 스레드로 실행 (수집 루프 비블록)
    - `_ENRICH_LOCK`으로 동시 namu 호출 직렬화 (공유 FlareSolverr 세션 보호)
  - `_auto_discover`: 신규 회차 발견 소설의 메타 갱신도 백그라운드로
- **`_estimate_seconds_per_chapter` TTL 캐시(60초)**: `/pipeline/status` 3초 폴링마다
  챕터 파일 전체를 읽던 것을 1분 1회로 제한 (CPU/IO 최적화)

### 연재 상태/메타데이터 정확도 개선 (소스 기반)
- **상태 진실 원천 변경**: namu.wiki → **discover(소스 사이트)**
  - `_update_novel_status_from_discover()`: 신규 회차 발견(added>0) → 연재중
  - 2개월 연속 신규 0 → **완결 자동 판정** → 이후 월간 체크 목록에서 완전 제외
  - `lib/storage.py::update_meta_from_namu()`: status 덮어쓰기 제거 (namu 연재상태는 stale/부정확)
- **메타데이터 갱신 트리거** (`run_enrich(force=True)` 추가):
  - URL 수신 시(`run_all`) → 항상 갱신
  - 월 1일 `_auto_discover` → **신규 회차 발견(queue 추가)된 소설만** namu 메타 갱신
- **현재 오류 데이터 수정**: 게임_속_바바리안(완결→연재중), 화산귀환(완결→연재중)
  - 확인: 게임 소설은 bookto31에 1000+화 존재(셀렉트는 최근 30화 윈도우만 표시) → "완결 30화"는 오류
  - 하남자(완결)는 유지 — 유일하게 정확한 완결
- **discover 결과**: `meta.last_discover`/`no_new_streak`/`last_new_episode` 기록

### Admin 페이지 단순화 + 연재 상태 구분
- **Admin 페이지 재구성**: 진행 중 / 완료 2개 섹션으로 정리
  - 진행 중인 작업: 소설명 + 진행바 + `N/M (P%)` + `예상 완료까지 X시간 Y분(약 Z일)` — 루프 상태/wr_id/시도/큐 세부 제거
  - 완료된 작업: `제목 · N화 · [완결]/[연재 중]`
- **진행 중/완료 판정**: `collection_done = queue 없음 && saved == total`
  - 완결이어도 queue가 남아 있으면 "진행 중" (게임: 완결인데 210개 대기)
- **연재 상태 구분 (완결/연재 중)**: `services/data.py::resolve_status()`
  - `meta.status`가 (완결/연재중/연재/단편) → 정규화, 누락/unknown이면 **수집 이력 fallback 추론** (14일↑ = 완결)
  - 라이브러리 표지 배지·소설 상세·Admin 완료 목록에 일관 적용, "연재중"→"연재 중" 표시
- **ETA**: `routers/pipeline.py::_estimate_seconds_per_chapter()` — 최근 수집 간격 평균 × 남은 queue 수 → `eta_seconds`
  - bookto31(300~600s)/toki31(5~60s) 실제 속도 반영, 백엔드 계산
- **`routers/pipeline.py`**: 미사용 import(asyncio/sys)·f-string 정리 (ruff clean)

### EPUB 회차 순서 버그 수정
- **`services/epub.py::build_epub`**: 챕터 정렬이 **wr_id**(파일명) 기준이던 것을 **chapter 번호** 기준으로 수정
  - 화산귀환(1922화가 wr_id 최소) 등에서 EPUB 챕터 순서가 뒤섞이는 문제 해결
  - 재빌드 후 5개 소설 전부 오름차순 확인 (게임 868~897, 아포 1~287, 오늘만 477~839, 하남자 1~557, 화산 1837~1922)

### EPUB 제작/재제작 정책 도입
- **제작 시점**: 전체 회차 수집 완료 시 (해당 소설 queue가 비워지는 순간)
  - `scripts/pipeline.py` collect 루프에 `_build_epub_for_drained_novels()` 훅 추가
  - URL 수신(`pipeline.py all`) 전체 collect 완료, 월 1일 `_auto_discover` 후 loop 수집 완료 시 자동 재제작
- **fingerprint 기반 재빌드 판정**: `maybe_build_epub()` — 챕터 수/최고 번호/최신 collected_at 비교, 변경 없으면 no-op
- **디스크 캐시**: `/opt/ai_data/flaresolverr/epub/{소설ID}.epub` — 다운로드는 캐시를 O(1) 서빙
- **표지**: webp→jpeg 변환 (Pillow) — EPUB 리더 호환성 확보, `cover_{소설ID}.jpg` 캐시
  - 표지 이미지가 없어도 **텍스트 타이틀 페이지** 항상 생성
- **다운로드 API**: `routers/novels.py` — 캐시 파일 `FileResponse` 서빙, 미제작 시 `409`
- **수동 재제작**: `python3 scripts/pipeline.py epub [novel_id ...]` (인자 없으면 전체)
- **`requirements.txt`**: `Pillow>=10.0.0` 추가

### 버그 수정 (양방향 검증 후)
- **회차 바로가기(jump) 버그 수정** (`NovelClient.tsx`): `Math.ceil(n/20)`과 `totalChapters`(개수) 검사 제거
  - 실제 `chapter` 번호로 목록에서 위치를 찾아 페이지 계산 → 화산귀환(1854~), 게임(868~)처럼
    1부터 시작하지 않는 작품에서도 "회차 바로가기" 정상 동작
- **게임_속_바바리안으로_살아남기 중복 제거**: wr_id 42424.json이 43353.json과 동일 내용(891화) 중복
  - 42424(legacy, source=None) 삭제, 43353(pipeline 수집) 유지 → 30개 회차로 정리
  - meta.json의 잘못된 `main_wr_id=42424`(실제로는 챕터 파일) 제거
- **`data.py`**: `get_novel_list()`의 사용하지 않는 `first_chapter` 변수 제거 (ruff clean)

### Neon DB 의존성 제거
- **정책 변경**: Neon DB 사용 중단 → 모든 데이터를 devforge 백엔드(파일시스템)에서 직접 제공
- **`app/api/[...slug]/route.ts`**: `proxyToNeon()`/`proxyNovelChapters()` 제거, 모든 경로 devforge 프록시로 단순화
- **`app/api/neon/novels/route.ts`**, **`app/api/novels/[id]/chapters/route.ts`** 삭제
- **`package.json`**: `@neondatabase/serverless` 의존성 제거
- **`services/ebook_sync.py`** 삭제 (Neon 동기화 모듈, 참조 없음)

### 회차 번호 오염 수정 (1화→2화 탐색 복구)
- **근본 원인**: `save_chapter`가 회차번호 추출 실패 시 `wr_id`로 폴백 → `chapter`/`title` 오염
- **`lib/storage.py`**: 폴백 제거, 추출 실패 시 `chapter=None` 유지 / `_extract_chapter_num` 다중 라인 지원
- **데이터 복구**: 아포칼립스의_고인물 286개 파일을 toki31 에피소드 API 기준으로 1~287화 재번호
- **`services/data.py`**: 이전/다음 화·목록 정렬을 wr_id → **chapter 번호** 기준으로 변경
  - 화산귀환(1922화가 wr_id 최소), 게임_속_바바리안(891화가 868화 앞) 등 순서 오류 해결
- **`NovelClient.tsx` / `ChapterClient.tsx`**: "회차 목록으로 돌아가기" 페이지 계산을 focus 챕터 위치 기반으로 수정

### 파이프라인 안정성 강화 (업계 표준 반영)
- **systemd WatchdogSec + on-watchdog**: `pipeline.py`에 `_sd_notify()` 추가
  - `Type=simple` → `Type=notify`, `Restart=on-failure` → `on-watchdog`, `WatchdogSec=600`
  - loop이 5분마다 `WATCHDOG=1` 신호 → 10분 내 미수신 시 hang 판정 후 재시작
- **queue 파일 락 (race 방지)**: `_load_queue`/`_save_queue`에 fcntl + atomic write
  - collect 락(`queue.collect.lock`)과 queue 락(`queue.lock`)을 **별도 파일로 분리**
  - flock이 같은 fd에 두 번째 flock을 걸면 이전 락을 대체하는 버그 수정
  - `run_collect` 전체를 단일 writer 락으로 직렬화 (toki31/bookto31 상호 덮어쓰기 방지)
- **DLQ (실패 항목 보존)**: `_add_to_dlq()` 추가
  - 3회 실패 챕터를 `failed.json`에 기록 후 queue에서 제거 (최대 5000개)
- **적응형 딜레이**: 고정 5분 → `10 × fetch 시간`
  - bookto31: `max(300, fetch×10)` 최소 5분 / 최대 10분 (Cloudflare 보호)
  - toki31: `max(5, min(60, fetch×10))` — 한번에 다 받는 형식
- **discover 전체 회차 확인**: max_pages 8/50 → 200 (epage/spage 모두 순회)
  - 백엔드 `/pipeline/start`도 max_pages 200, timeout 1500s
  - 월 1회 auto-discover가 전체 회차 정확히 확인
- **커밋**: `6cdda03`(hardening) → `5cdd0c4`(락 분리 수정) → `d44a7e6`(문서)

### watchdog (devforge) ebook 전용 감시
- `/opt/projects/server/scripts/lib/watchdog/checker.py`: `check_ebook_pipeline()` 추가
  - systemd active + `pipeline.py loop` 프로세스 존재 + 마지막 로그 활동(20분) 확인
  - `check_all_services()`에서 ebook-watcher만 전용 체크 사용
- `config.py`: `ebook-watcher.timer` TIMER_TARGETS 제거 (15분 로직 삭제)
- watchdog이 메인으로 ebook 파이프라인을 감시/관리 (이중 감시: systemd + watchdog)

### 수집 데이터 복구
- **아포칼립스의 고인물** (toki31, novel_id 58455): 전체 287화 수집 완료
  - 기존 101개 → 287개 (누락 186개 재수집)
- **화산귀환** (bookto31): 전체 회차 확인 (spage 순회) → queue에 1900+개
- **당문출사**: 테스트 데이터 삭제

## 2026-09-06

### 공통 레이어 리팩터링 (Phase 1~5 + 잔여 caller 이관)
- **`lib/user_agent.py`** (신규): Chrome 헤더 빌더 (`chrome_headers()`, `namu_headers()`)
- **`lib/flaresolverr_client.py`** (신규): `FlareSolverrSession` 클래스 — 세션 관리 + rate limit 연동
- **`lib/curl_session.py`** (신규): curl_cffi 세션 팩토리 (`create_curl_session(impersonate="chrome131")`)
- **`lib/storage.py`** (신규): 챕터 저장/메타 관리 (`save_chapter()`, `update_meta_from_namu()`)
- **`services/bookto31.py`** (리팩터): inline FlareSolverr 로직 → `FlareSolverrSession` 사용 (170줄 제거)
- **`services/metadata_namu.py`** (리팩터): `from services.bookto31 import _fetch_with_flaresolverr` 제거 → `FlareSolverrSession(rate_limit=False)` 사용
- **`services/toki31.py`** (재작성): requests+proxy → curl_cffi (`lib.curl_session`) + RSC 파서 추가 (187줄 제거)
- **`scripts/ebook_watcher/ebook_worker.py`** (리팩터): 저장 로직 → `lib.storage` 사용, health check → `FlareSolverrSession` (79줄 제거)
- **`scripts/discover_chapters.py`** (리팩터): `bookto31._fetch_with_flaresolverr` → `FlareSolverrSession(rate_limit=False)`
- **`scripts/dual_metadata_ssot.py`** (리팩터): `bookto31._fetch_with_flaresolverr` → `FlareSolverrSession(rate_limit=False)`
- **커밋**: `8483f85`(Ph1) → `19df167`(Ph2) → `7602a4c`(Ph3) → `08e5c8b`(Ph4) → `cf9eba7`(Ph5) → `570cd4e`(잔여 caller)
- **총 코드 감소**: 약 463줄 (인라인 중복 제거 + 공통 레이어 분리)

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