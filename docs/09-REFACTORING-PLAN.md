# ebooklib 리팩터링 — 공통 레이어 분리 (완료)

> **작성일**: 2026-09-06
> **완료일**: 2026-09-06
> **커밋**: `8483f85`(Ph1) → `19df167`(Ph2) → `7602a4c`(Ph3) → `08e5c8b`(Ph4) → `cf9eba7`(Ph5) → `570cd4e`(잔여 caller 이관)
> **관련 문서**: [08-TOKI31-ANALYSIS.md](08-TOKI31-ANALYSIS.md), [02-BOT-BYPASS.md](02-BOT-BYPASS.md)

## 1. 배경

### 문제점

1. **bookto31.py와 toki31.py가 중복 코드 공유** — HTTP 헤더, 세션 관리, rate limiter 호출 등
   - bookto31.py는 `_create_session()` 안에 헤더가 inline 하드코딩 (별도 `_build_headers()` 없음)
   - toki31.py는 `_build_headers()` 별도 함수 보유 — 두 곳의 헤더가 중복
2. **metadata_namu.py가 bookto31의 private 함수를 import** (`from services.bookto31 import _fetch_with_flaresolverr`) — 캡슐화 위반
3. **toki31.py가 구식 구현** — requests + 무료 KR proxy만 의존.
   - **2026-09-06 PoC 결과**: curl_cffi chrome131 TLS fingerprint 위장으로 Oracle datacenter IP에서도 접속 성공 (HTTP 200, `/novel` RSC payload 28개/에피소드 참조 84개) → curl_cffi 도입 근거 확보
4. **ebook_worker.py에 저장 로직이 내장** — JSON 저장 + meta.json 갱신 + Neon DB 동기화가 한 함수(`save_chapter`)에 집중
   - 기존 계획서에서 `collect_novel.py`에 "유사 로직 중복"이라 적었으나 **collect_novel.py는 실제로 존재하지 않음** → 저장 로직 중복은 ebook_worker.py 내부 응집 문제로 정정

---

## 2. 이전 아키텍처 (AS-IS) — 변경 완료

> ⚠️ 아래는 변경 전 구조입니다. 현재는 섹션 3의 아키텍처를 사용합니다.

```
apps/backend/
├── lib/
│   └── rate_limiter.py          # SQLite 기반 rate limiter (8분 + jitter)
│
├── services/
│   ├── bookto31.py              # FlareSolverr + GNUBOARD5 파싱 (모든 로직 inline)
│   │   ├── _create_session()    # ← Chrome 헤더 inline (toki31 _build_headers와 중복)
│   │   ├── _session_*           # FlareSolverr 세션 관리
│   │   ├── _rate_limit_*()      # rate limiter 호출 (inline 구현 + lib import)
│   │   ├── _flaresolverr_*()    # FlareSolverr HTTP 클라이언트
│   │   ├── fetch_*()            # 5개 public fetch 함수
│   │   └── parse_*()            # GNUBOARD5 HTML 파서
│   │
│   ├── toki31.py                # requests + 무료 KR proxy (구식)
│   │   ├── _build_headers()     # ← bookto31과 거의 동일한 Chrome 헤더
│   │   ├── _proxy_*             # proxy pool 관리
│   │   ├── _fetch_with_failover() # proxy failover
│   │   └── fetch_*()            # 4개 public fetch 함수
│   │
│   ├── metadata_namu.py         # namu.wiki 메타데이터 (FlareSolverr 공유)
│   │   └── imports _fetch_with_flaresolverr from bookto31 ← ❌
│   │
│   ├── data.py                  # JSON 파일 읽기 서비스 (웹페이지용)
│   ├── ebook_sync.py            # Neon DB 동기화
│   ├── epub.py                  # EPUB 생성
│   └── metadata.py              # Google Books/OpenLibrary 메타데이터

scripts/
├── ebook_watcher/
│   ├── ebook_worker.py          # 큐 기반 수집 워커 (저장 로직 + namu 메타 내장)
│   ├── ebook_queue.py           # 큐 CLI
│   └── watchdog.py              # 1분 주기 트리거
│
├── bookto31_healthcheck.py      # 북토끼 상태 체크
├── discover_chapters.py         # 회차 목록 발견 (FlareSolverr 직접 호출)
├── dual_metadata_ssot.py        # 듀얼 메타데이터
├── brave_book_url_search.py     # Brave 검색
└── json_to_epub.py              # JSON → EPUB 변환

# 참고: collect_novel.py는 존재하지 않음 (계획서 초안의 오기)
#   독립 수집은 ebook_worker.py(큐 기반) + discover_chapters.py(회차 발견)로 분담

프로젝트 외부:
├── systemd: ebook-watcher.service     # watchdog.py 15분 실행
├── systemd: ebook-api.service         # FastAPI port 8089
├── systemd: container-flaresolverr    # FlareSolverr 컨테이너
├── caddy: /etc/caddy/Caddyfile        # 8089 reverse proxy
└── watchdog: config.py                # ebook-watcher 감시 등록
```

### 데이터 흐름

```
bookto31.py ──→ FlareSolverr ──→ bookto31.com (Cloudflare Turnstile)
                                        ↓
                                   JSON 파일
                              (/opt/ai_data/flaresolverr/novels/)
                                        ↓
toki31.py ────→ curl_cffi ────→ toki31.com (CloudFront KR_ONLY)  ← curl_cffi PoC 성공(2026-09-06)
                                        ↓
                                   JSON 파일 (동일 경로)
                                        ↓
                              services/data.py → FastAPI → Next.js → 브라우저
```

### 의존성 그래프 (현재 — 변경 완료)

```
lib/user_agent.py ───────────────────────────── bookto31.py
     │                                              │
     └──────────────────────────────────────────── toki31.py

lib/flaresolverr_client.py ─────────────────── bookto31.py
     │                                              │
     ├──────────────────────────────────── metadata_namu.py
     │                    (rate_limit=False)         │
     │                                              └── namu.wiki 자체 30분 rate limiter 사용
     ├──────────────────────────────────── discover_chapters.py
     │                    (rate_limit=False)
     ├──────────────────────────────────── dual_metadata_ssot.py
     │                    (rate_limit=False)
     ├──────────────────────────────────── ebook_worker.py
     │                    (rate_limit=False, lazy init)
     └──────────────────────────────────── MCP (flaresolverr_bypass)

lib/curl_session.py ───────────────────────── toki31.py

lib/storage.py ────────────────────────────── ebook_worker.py
     │
     └─────────────────────────────────────── toki31.py (권장)
```

### 보존 규칙

| 규칙 | 설명 |
|------|------|
| ✅ `lib/` 모듈은 `services/`를 import하지 않음 | 하위 계층이 상위 계층 참조 금지 |
| ✅ `services/`는 `lib/`만 import | 서비스 로직은 lib 위에만 의존 |
| ✅ `scripts/`는 `lib/` 또는 `services/` import | 자유 |
| ✅ 웹페이지 로직(`routers/`, `frontend/`) 변경 불가 | bookto31/toki31과 무관 |
| ✅ `_` prefix private 함수를 다른 모듈에서 import 금지 | 캡슐화 위반 |
| ✅ 잔여 caller 모두 FlareSolverrSession으로 이관 완료 | `discover_chapters.py`, `ebook_worker.py`, `dual_metadata_ssot.py` |

---

## 3. 현재 아키텍처 (변경 완료)

> ✅ 모든 Phase 완료 (Phase 1~5 + 잔여 caller 이관). 아래가 현재 코드 상태.

```
apps/backend/
├── lib/
│   ├── __init__.py
│   ├── rate_limiter.py          # ✅ 기존 유지 (변경 없음)
│   ├── user_agent.py            # ← NEW: 공통 Chrome 헤더 빌더
│   ├── flaresolverr_client.py   # ← NEW: FlareSolverr 세션 관리
│   ├── curl_session.py          # ← NEW: curl_cffi 세션 팩토리
│   └── storage.py               # ← NEW: 챕터 저장/메타 관리
│
├── services/
│   ├── bookto31.py              # REFACTOR: FlareSolverr 호출 + GNUBOARD5 파싱만
│   │   ├── from lib.user_agent import chrome_headers
│   │   ├── from lib.flaresolverr_client import FlareSolverrSession
│   │   ├── fetch_*()            # public fetch 함수만 유지
│   │   └── parse_*()            # GNUBOARD5 HTML 파서
│   │
│   ├── toki31.py                # REFACTOR: curl_cffi + Next.js RSC 파싱
│   │   ├── from lib.user_agent import chrome_headers
│   │   ├── from lib.curl_session import create_curl_session
│   │   ├── from lib.storage import save_chapter     (권장)
│   │   ├── fetch_*()            # public fetch 함수
│   │   └── parse_*()            # Next.js RSC payload 파서
│   │
│   ├── metadata_namu.py         # REFACTOR: lib.flaresolverr_client 사용
│   │   ├── from lib.flaresolverr_client import FlareSolverrSession
│   │   ├── ⚠️ FlareSolverrSession(rate_limit=False) — namu.wiki는 자체 30분 제한 사용
│   │   └── bookto31 import 제거 ✅
│   │
│   ├── data.py                  # ✅ 변경 없음
│   ├── ebook_sync.py            # ✅ 변경 없음
│   ├── epub.py                  # ✅ 변경 없음
│   └── metadata.py              # ✅ 변경 없음

scripts/
├── ebook_watcher/
│   ├── ebook_worker.py          # REFACTOR 완료: lib.storage + FlareSolverrSession 사용
│   ├── ebook_queue.py           # ✅ 변경 없음
│   └── watchdog.py              # ✅ 변경 없음
│
├── bookto31_healthcheck.py      # ✅ 변경 없음 (requests로 health check)
├── discover_chapters.py         # REFACTOR 완료: FlareSolverrSession(rate_limit=False) 사용
├── dual_metadata_ssot.py        # REFACTOR 완료: FlareSolverrSession(rate_limit=False) 사용
├── brave_book_url_search.py     # ✅ 변경 없음
└── json_to_epub.py              # ✅ 변경 없음

# 참고: collect_novel.py는 존재하지 않음. 독립 수집이 필요하면 Phase 1에서 신규 생성

프로젝트 외부:
├── systemd: ebook-watcher.service     # ✅ 변경 없음
├── systemd: ebook-api.service         # ✅ 변경 없음
├── systemd: container-flaresolverr    # ✅ 변경 없음
├── caddy: /etc/caddy/Caddyfile        # ✅ 변경 없음
└── watchdog: config.py                # ✅ 변경 없음
```

---

## 4. 공통 레이어 상세

> ✅ 모든 레이어 생성 완료. 아래는 현재 코드 상태.

### 4.1 `lib/user_agent.py` ✅ 완료

```python
"""Chrome 브라우저 헤더 빌더 — bookto31/toki31 공용"""

def chrome_headers(version: str = "131") -> dict:
    """Chrome 브라우저 헤더 반환.
    
    Args:
        version: "120" | "124" | "131" (기본)
    
    Returns:
        Accept, Accept-Language, User-Agent, Sec-CH-UA 등 완전한 헤더 dict
    """
    ua_map = {
        "120": "Chrome/120.0.0.0",
        "124": "Chrome/124.0.0.0",
        "131": "Chrome/131.0.0.0",
    }
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) {ua_map.get(version, ua_map['131'])} Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,...",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": f'"Not_A Brand";v="8", "Chromium";v="{version}", "Google Chrome";v="{version}"',
        # ... 전체 헤더
    }
```

**영향 파일**: toki31.py (curl_cffi 사용)

### 4.2 `lib/flaresolverr_client.py` ✅ 완료

```python
"""FlareSolverr 클라이언트 — Cloudflare Turnstile 우회 공유 세션 관리"""

class FlareSolverrSession:
    """FlareSolverr HTTP API 세션 관리
    
    - 세션 생성/유지/파기
    - 쿠키/UA 캐싱 (cf_clearance 재사용)
    - rate limiter 연동 (선택)
    """
    
    FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
    
    def __init__(self, rate_limit: bool = True):
        self._session_id: Optional[str] = None
        self._cookies: Dict[str, str] = {}
        self._ua: str = ""
        self._lock = threading.Lock()
        self._rate_limit = rate_limit
    
    def fetch(self, url: str, max_attempts: int = 3) -> Optional[str]:
        """URL을 FlareSolverr로 요청, HTML 본문 반환"""
        if self._rate_limit:
            wait_if_needed(url)
        # ... FlareSolverr POST + 세션 갱신 + 재시도
        if self._rate_limit:
            record_request(url, status=200)
        return html
    
    def create_session(self) -> str: ...
    def destroy_session(self) -> None: ...
    def get_cookies(self) -> Dict[str, str]: ...
```

> ⚠️ **metadata_namu.py 주의**: `FlareSolverrSession(rate_limit=False)`로 생성할 것.
> namu.wiki는 **자체 30분 rate limiter**(`_namu_rate_limit()`, 별도 DB `/opt/ai_data/flaresolverr/namu_rate_limiter.db`)를 먼저 호출한 뒤 FlareSolverr를 사용한다.
> `rate_limit=True`(기본값)로 생성하면 bookto31의 8분 제한이 namu.wiki 요청에 적용되어 간격이 비정상적으로 짧아진다.
> 
> 예상 코드:
> ```python
> _fs = FlareSolverrSession(rate_limit=False)  # namu.wiki 전용
> _namu_rate_limit()                           # 30분 자체 제한
> html = _fs.fetch(url)
> _namu_record()                               # 자체 기록
> ```

**영향 파일**: bookto31.py (사용), metadata_namu.py (사용, rate_limit=False), discover_chapters.py (사용), dual_metadata_ssot.py (사용), ebook_worker.py (사용, lazy init), MCP (참조)

### 4.3 `lib/curl_session.py` ✅ 완료

```python
"""curl_cffi 세션 팩토리 — TLS fingerprint 위장"""

from curl_cffi import requests as creq

def create_curl_session(
    impersonate: str = "chrome131",
    headers: Optional[dict] = None,
    proxy: Optional[str] = None,
) -> creq.Session:
    """curl_cffi Session 생성 + 기본 헤더 설정
    
    Args:
        impersonate: "chrome131" | "chrome120" | "safari17_0" | "firefox135"
        headers: 추가 헤더 (기본: Accept-Language=ko-KR)
        proxy: 프록시 URL (예: "socks5://...")
    """
    from lib.user_agent import chrome_headers
    session = creq.Session(impersonate=impersonate)
    session.headers.update(chrome_headers(impersonate.replace("chrome", "")))
    if headers:
        session.headers.update(headers)
    if proxy:
        session.proxies = {"https": proxy, "http": proxy}
    return session
```

**영향 파일**: toki31.py (사용)

### 4.4 `lib/storage.py` ✅ 완료

```python
"""챕터 저장/메타데이터 관리 — 모든 수집기 공용"""

NOVELS_DIR = Path("/opt/ai_data/flaresolverr/novels")
COVERS_DIR = Path("/opt/ai_data/flaresolverr/covers")

def save_chapter(
    novel_title: str,
    wr_id: int,
    body: str,
    source: str = "bookto31",
    chapter_num: Optional[int] = None,
) -> bool:
    """챕터 본문을 JSON 파일로 저장 + meta.json 갱신"""
    # ...

def update_meta(novel_dir: Path, chapter_count: int) -> dict:
    """meta.json totalChapters 갱신"""
    # ...

def get_novel_dir(novel_title: str) -> Path:
    """소설명 → 디렉토리 경로"""
    # ...
```

**영향 파일**: ebook_worker.py (사용), toki31.py (권장)

---

## 5. 변경 완료

### 5.1 변경된 파일 요약

| 파일 | 변경 내용 | 커밋 |
|------|----------|------|
| `lib/user_agent.py` | 신규 생성: Chrome 헤더 빌더 | `8483f85` (Phase 1) |
| `lib/flaresolverr_client.py` | 신규 생성: FlareSolverrSession | `8483f85` (Phase 1) |
| `lib/curl_session.py` | 신규 생성: curl_cffi 세션 팩토리 | `8483f85` (Phase 1) |
| `lib/storage.py` | 신규 생성: 챕터 저장/메타 관리 | `8483f85` (Phase 1) |
| `services/bookto31.py` | FlareSolverrSession 사용으로 리팩터 | `19df167` (Phase 2) |
| `services/metadata_namu.py` | bookto31 import 제거, FlareSolverrSession 사용 | `7602a4c` (Phase 3) |
| `services/toki31.py` | curl_cffi 도입, proxy 제거, RSC 파서 추가 | `08e5c8b` (Phase 4) |
| `scripts/ebook_watcher/ebook_worker.py` | lib.storage 사용, FlareSolverrSession 사용 | `cf9eba7` (Phase 5) |
| `scripts/discover_chapters.py` | FlareSolverrSession(rate_limit=False) 사용 | `570cd4e` |
| `scripts/dual_metadata_ssot.py` | FlareSolverrSession(rate_limit=False) 사용 | `570cd4e` |

### 5.2 변경 불필요한 파일 (웹페이지 로직)

| 파일 | 사유 |
|------|------|
| `routers/novels.py` | bookto31/toki31 미참조 |
| `routers/chapters.py` | bookto31/toki31 미참조 |
| `routers/metadata.py` | bookto31/toki31 미참조 |
| `services/data.py` | JSON 파일 읽기만 수행 |
| `services/ebook_sync.py` | bookto31/toki31 미참조 |
| `services/epub.py` | bookto31/toki31 미참조 |
| `services/metadata.py` | bookto31/toki31 미참조 |
| `main.py` | 라우터 등록만 수행 |
| `frontend/` 전체 | API 호출만 수행 |
| `scripts/bookto31_healthcheck.py` | requests로 health check, bookto31 미참조 |

### 5.3 bookto31.py 리팩터 후 코드 (변경 완료) ✅

> **FlareSolverrSession이 자체 UA 관리 → lib.user_agent 불필요**

```python
"""bookto31.com 크롤러 — 순수 GNUBOARD5 파싱만"""

from lib.flaresolverr_client import FlareSolverrSession
# (FlareSolverrSession이 자체 UA 관리 → lib.user_agent 불필요)

BASE_URL = "https://bookto31.com"
_fs = FlareSolverrSession(rate_limit=True)

def fetch_home() -> Optional[str]:
    return _fs.fetch(f"{BASE_URL}/")

def fetch_novel_index(novel_id: int) -> Optional[str]:
    return _fs.fetch(f"{BASE_URL}/bbs/board.php?bo_table=novel&wr_id={novel_id}")

def fetch_chapter(wr_id: int) -> Optional[str]:
    return _fs.fetch(f"{BASE_URL}/bbs/board.php?bo_table=novel&wr_id={wr_id}")

# --- 파서 (변경 없음) ---
def parse_chapter_list(html, novel_id) -> List[Dict]: ...
def parse_chapter_body(html) -> str: ...
def parse_novel_meta(html) -> Dict: ...
def extract_chapter_wr_ids_from_index(html) -> List[Tuple[int, int]]: ...
def find_chapter_wr_id(html, novel_main_wr_id, chapter_num) -> Optional[int]: ...
def is_novel_index_page(html) -> bool: ...
```

### 5.4 toki31.py 리팩터 후 코드 (변경 완료) ✅

> **2026-09-06 PoC 검증 완료** ✅
> curl_cffi chrome131로 Oracle datacenter IP에서 toki31.com 접속 성공:
> - `/` (루트): HTTP 200, 277KB
> - `/novel`: HTTP 200, 215KB — RSC payload 28개 스크립트, 에피소드 참조 84개 발견
> - 무료 KR proxy 불필요 확인 (TLS fingerprint 위장만으로 CloudFront geo-block 우회)

```python
"""toki31.com 크롤러 — 순수 Next.js RSC 파싱만"""

from lib.user_agent import chrome_headers
from lib.curl_session import create_curl_session

BASE_URL = "https://toki31.com"
_session = create_curl_session(impersonate="chrome131")

def fetch_home() -> Optional[str]:
    return _session.get(f"{BASE_URL}/", timeout=15)

def fetch_ing() -> Optional[str]:
    """연재중 웹툰/소설 목록 (RSC payload 포함)"""
    return _session.get(f"{BASE_URL}/ing", timeout=15)

# --- 파서 (Next.js RSC 전용) ---
def parse_rsc_payload(html) -> List[Dict]:
    """RSC payload에서 에피소드 데이터 추출"""
    # scripts[55] 등에 있는 RSC 데이터 파싱
    ...

def extract_episode_data(html) -> List[Dict]:
    """/ing 페이지에서 episode 데이터 추출
    [{episodeCount, latestEpisodeNumber, rating, ...}, ...]
    """
    ...
```

---

## 6. 마이그레이션 순서 (모두 완료)

### Phase 1: 공통 레이어 생성 ✅ 완료 (`8483f85`)

| 단계 | 작업 | 상태 |
|------|------|------|
| 1 | `lib/user_agent.py` 생성 | ✅ |
| 2 | `lib/flaresolverr_client.py` 생성 | ✅ |
| 3 | `lib/curl_session.py` 생성 | ✅ |
| 4 | `lib/storage.py` 생성 | ✅ |
| 5 | `git tag phase1-complete` 생성 | ✅ |

### Phase 2: bookto31.py 리팩터 ✅ 완료 (`19df167`)

| 단계 | 작업 | 상태 |
|------|------|------|
| 1 | bookto31.py: `_create_session()` inline 헤더 → `lib.flaresolverr_client.FlareSolverrSession` | ✅ |
| 2 | 테스트: `from services.bookto31 import fetch_home` | ✅ |
| 3 | `git tag phase2-complete` 생성 | ✅ |

### Phase 3: metadata_namu.py 리팩터 ✅ 완료 (`7602a4c`)

| 단계 | 작업 | 상태 |
|------|------|------|
| 1 | `from services.bookto31 import _fetch_with_flaresolverr` → `from lib.flaresolverr_client import FlareSolverrSession` | ✅ |
| 2 | `FlareSolverrSession(rate_limit=False)` 사용 | ✅ |
| 3 | 테스트: namu.wiki 메타데이터 조회 정상 동작 | ✅ |
| 4 | `git tag phase3-complete` 생성 | ✅ |

### Phase 4: toki31.py 재작성 ✅ 완료 (`08e5c8b`)

| 단계 | 작업 | 상태 |
|------|------|------|
| 1 | toki31.py: curl_cffi 도입 (`lib.curl_session`) | ✅ |
| 2 | toki31.py: 무료 KR proxy 로직 제거 | ✅ |
| 3 | toki31.py: Next.js RSC 파서 추가 | ✅ |
| 4 | 테스트: curl_cffi로 toki31.com 접속 확인 | ✅ |
| 5 | `git tag phase4-complete` 생성 | ✅ |

### Phase 5: 저장 로직 통합 ✅ 완료 (`cf9eba7`)

| 단계 | 작업 | 상태 |
|------|------|------|
| 1 | ebook_worker.py: `save_chapter()` → `lib.storage.save_chapter()` | ✅ |
| 2 | `git tag phase5-complete` 생성 | ✅ |

### 잔여 caller 이관 ✅ 완료 (`570cd4e`)

| 파일 | 변경 내용 | 상태 |
|------|----------|------|
| `scripts/discover_chapters.py` | `bookto31._fetch_with_flaresolverr` → `FlareSolverrSession` | ✅ |
| `scripts/dual_metadata_ssot.py` | `bookto31._fetch_with_flaresolverr` → `FlareSolverrSession` | ✅ |
| `scripts/ebook_watcher/ebook_worker.py` | `_check_bookto31_alive()` → `FlareSolverrSession` (lazy init) | ✅ |

---

## 7. 롤백 절차 (참고용)

각 Phase 시작 전 `git tag`를 생성하고, 실패 시 아래 순서로 복구한다.

### 공통 롤백 순서
```bash
# 1. 변경사항 스태시
git stash

# 2. Phase 직전 태그로 복구
git checkout <phase-before-tag>

# 3. systemd 서비스 재시작 (ebook-watcher만 해당)
systemctl --user restart ebook-watcher.service

# 4. 정상 동작 확인
python3 -c "from services.bookto31 import fetch_home; print('OK:', fetch_home()[:100] if fetch_home() else 'FAIL')"
```

### Phase별 태그명
| Phase | 시작 전 태그 | 실패 시 복구 대상 |
|-------|------------|------------------|
| Phase 1 | `phase1-complete` (완료 시점) | `git revert`로 Phase 1만 취소 가능 |
| Phase 2 | `phase2-before` | Phase 1 lib 파일은 유지, bookto31.py만 복구 |
| Phase 3 | `phase3-before` | metadata_namu.py만 복구 |
| Phase 4 | `phase4-before` | toki31.py + lib/curl_session.py 복구 |
| Phase 5 | `phase5-before` | ebook_worker.py + lib/storage.py 복구 |

### Phase 2·4 실패 시 특이사항
- Phase 2 실패: bookto31.py의 FlareSolverrSession 호출부가 잘못되면 **FlareSolverr 연결이 끊김** → `git checkout phase2-before` 후 FlareSolverr 컨테이너 재시작도 고려
  ```bash
  podman restart container-flaresolverr
  ```
- Phase 4 실패: toki31.py가 requests에서 curl_cffi로 변경된 후 **기존 proxy 로직이 제거됨** → 복구 시 `git checkout phase4-before`로 완전 복원

---

## 8. 검증 기준 (현재)

### 전체 검증 스크립트

```bash
cd /opt/workspace/ebooklib/apps/backend && python3 -c "
print('=== 양방향 검증 ===')

# 1. lib 모듈 독립 import
from lib import user_agent, flaresolverr_client, curl_session, storage
print('[OK] lib 모듈 독립 import')

# 2. services → lib 의존성
from services import bookto31, toki31, metadata_namu
print('[OK] services → lib 의존성')

# 3. 잔여 caller 이관 확인
import sys
sys.path.insert(0, '/opt/workspace/ebooklib')
import scripts.discover_chapters as dc
import scripts.dual_metadata_ssot as dmss
assert dc._fs._rate_limit == False
assert dmss._fs._rate_limit == False
print('[OK] 잔여 caller FlareSolverrSession 사용')

# 4. 역방향: lib이 services를 import하지 않음
import inspect
for name, mod in [('user_agent', user_agent), ('flaresolverr_client', flaresolverr_client), ('curl_session', curl_session), ('storage', storage)]:
    src = inspect.getsource(mod)
    assert 'from services' not in src
    print(f'[OK] {name} → services import 없음')

# 5. metadata_namu가 bookto31을 import하지 않음
src_namu = inspect.getsource(metadata_namu)
assert 'from services.bookto31' not in src_namu
print('[OK] metadata_namu → bookto31 import 없음')

print()
print('=== 검증 완료 ===')
"
```

### 개별 Phase 검증

```bash
# bookto31 정상 수집
python3 -c "
from services.bookto31 import fetch_home
html = fetch_home()
assert html and len(html) > 1000
print(f'OK: {len(html)} chars')
"

# toki31 curl_cffi 접속
python3 -c "
from lib.curl_session import create_curl_session
s = create_curl_session(impersonate='chrome131')
r = s.get('https://toki31.com/', timeout=15)
assert r.status_code == 200
print(f'OK: {len(r.text)} chars')
"

# namu.wiki 메타데이터
python3 -c "
from services.metadata_namu import _namu_fs
assert _namu_fs._rate_limit == False
print('OK: namu.wiki FlareSolverrSession(rate_limit=False)')
"
```

---

## 9. 보존 규칙

| 규칙 | 설명 | 상태 |
|------|------|------|
| `lib/` 모듈은 `services/`를 import하지 않음 | 하위 계층이 상위 계층 참조 금지 | ✅ |
| `services/`는 `lib/`만 import | 서비스 로직은 lib 위에만 의존 | ✅ |
| `scripts/`는 `lib/` 또는 `services/` import | 자유 | ✅ |
| 웹페이지 로직(`routers/`, `frontend/`) 변경 불가 | bookto31/toki31과 무관 | ✅ |
| `_` prefix private 함수를 다른 모듈에서 import 금지 | 캡슐화 위반 | ✅ |
| 잔여 caller 모두 FlareSolverrSession으로 이관 완료 | `discover_chapters.py`, `ebook_worker.py`, `dual_metadata_ssot.py` | ✅ |

---

## 10. 기존 코드 AS-IS (참고)

> 아래는 변경 전 코드 구조입니다. 복구가 필요할 때 참조하세요.

### 이전 아키텍처

```
apps/backend/
├── lib/
│   └── rate_limiter.py          # SQLite 기반 rate limiter (8분 + jitter)
│
├── services/
│   ├── bookto31.py              # FlareSolverr + GNUBOARD5 파싱 (모든 로직 inline)
│   │   ├── _create_session()    # ← Chrome 헤더 inline (toki31 _build_headers와 중복)
│   │   ├── _session_*           # FlareSolverr 세션 관리
│   │   ├── _rate_limit_*()      # rate limiter 호출 (inline 구현 + lib import)
│   │   ├── _flaresolverr_*()    # FlareSolverr HTTP 클라이언트
│   │   ├── fetch_*()            # 5개 public fetch 함수
│   │   └── parse_*()            # GNUBOARD5 HTML 파서
│   │
│   ├── toki31.py                # requests + 무료 KR proxy (구식)
│   │   ├── _build_headers()     # ← bookto31과 거의 동일한 Chrome 헤더
│   │   ├── _proxy_*             # proxy pool 관리
│   │   ├── _fetch_with_failover() # proxy failover
│   │   └── fetch_*()            # 4개 public fetch 함수
│   │
│   ├── metadata_namu.py         # namu.wiki 메타데이터 (FlareSolverr 공유)
│   │   └── imports _fetch_with_flaresolverr from bookto31 ← ❌
│   │
│   ├── data.py                  # JSON 파일 읽기 서비스 (웹페이지용)
│   ├── ebook_sync.py            # Neon DB 동기화
│   ├── epub.py                  # EPUB 생성
│   └── metadata.py              # Google Books/OpenLibrary 메타데이터

scripts/
├── ebook_watcher/
│   ├── ebook_worker.py          # 큐 기반 수집 워커 (저장 로직 + namu 메타 내장)
│   ├── ebook_queue.py           # 큐 CLI
│   └── watchdog.py              # 1분 주기 트리거
│
├── bookto31_healthcheck.py      # 북토끼 상태 체크
├── discover_chapters.py         # 회차 목록 발견 (FlareSolverr 직접 호출)
├── dual_metadata_ssot.py        # 듀얼 메타데이터
├── brave_book_url_search.py     # Brave 검색
└── json_to_epub.py              # JSON → EPUB 변환
```

### 이전 의존성 그래프

```
bookto31.py ─────────────────────────────────── metadata_namu.py
     │  (private 함수 import)                         │
     │                                               └── FlareSolverr만 사용
     │
     ├── lib/rate_limiter.py (import)
     │
     └── ebook_worker.py (import)
          │
          └── 저장 로직 (save_chapter, enrich_metadata_from_namu)

toki31.py ─── 고립됨 (다른 모듈에서 import 안 함)
     │
     └── requests + 무료 KR proxy (curl_cffi 미사용)
```