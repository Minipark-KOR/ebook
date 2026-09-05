#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/bookto31.py
"""bookto31.com (북토끼) 크롤러 - FlareSolverr Cloudflare 우회 + GNUBOARD5 파싱

배경:
- bookto31.com은 Cloudflare Turnstile Challenge로 보호된다.
- 자동화 도구(requests, curl)는 403을 받고 쿠키/세션이 없으면 본문이 비어있다.
- FlareSolverr (http://127.0.0.1:8191)을 통해 헤드리스 브라우저로 challenge를 풀고
  cf_clearance 쿠키를 받은 뒤 페이지를 가져온다.
- 본문 사이트는 GNUBOARD5 + APMS 테마. 회차 목록은 `<div class="view-content book-list">`,
  본문은 `<div class="view-content book-text-viewer">`.

FlareSolverr 응답 구조 (FlareSolverr v3.x):
- cmd=request.get → solution: {status, url, cookies: [...], response: <HTML>, userAgent}
- cookies는 [{domain, name, value, ...}, ...] 형태. request.headers["Cookie"]로 전달 가능.
"""

import threading
import time
from pathlib import Path
from typing import Optional, Union, List, Dict

import requests


BASE_URL = "https://bookto31.com"
FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
DEFAULT_TIMEOUT_MS = 60000

# Rate limiter (Cloudflare 차단 회피: 8분 간격 + ±2분 jitter)
_RATE_LIMITER_PATH = Path("/opt/ai_data/flaresolverr/rate_limiter.db")
_BOOKTO31_INTERVAL = 480  # 8분
_BOOKTO31_JITTER = 120  # ±2분


def _rate_limit_check(url: str) -> None:
    """북토끼 요청 시 rate limit 적용.

    같은 URL에 마지막 요청 이후 8분 + 0~2분이 지나야 통과.
    """
    try:
        # lib 패키지가 있으면 사용, 없으면 인라인 구현
        from lib.rate_limiter import wait_if_needed, record_request
        wait_sec = wait_if_needed(url, interval=_BOOKTO31_INTERVAL, db_path=_RATE_LIMITER_PATH)
        if wait_sec > 0:
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(asyncio.sleep(0))
            except RuntimeError:
                pass
            import time as _time
            _time.sleep(wait_sec)
    except ImportError:
        # lib 패키지 없으면 인라인 구현
        _inline_rate_limit(url)


def _inline_rate_limit(url: str) -> None:
    """rate_limiter 모듈 없을 때 폴백."""
    import sqlite3
    import random
    try:
        _RATE_LIMITER_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_RATE_LIMITER_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status INTEGER,
                ts REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_url_ts ON request_log(url, ts DESC)")
        row = conn.execute(
            "SELECT ts FROM request_log WHERE url = ? ORDER BY ts DESC LIMIT 1",
            (url,),
        ).fetchone()
        if row:
            elapsed = time.time() - row[0]
            if elapsed < _BOOKTO31_INTERVAL:
                wait = (_BOOKTO31_INTERVAL - elapsed) + random.uniform(0, _BOOKTO31_JITTER)
                print(f"[rate_limit] waiting {wait:.0f}s for {url}", flush=True)
                time.sleep(wait)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _rate_limit_record(url: str, status: int = 200) -> None:
    """북토끼 요청 후 기록."""
    try:
        from lib.rate_limiter import record_request
        record_request(url, status=status, db_path=_RATE_LIMITER_PATH)
    except ImportError:
        try:
            import sqlite3
            conn = sqlite3.connect(str(_RATE_LIMITER_PATH))
            conn.execute(
                "INSERT INTO request_log (url, status, ts) VALUES (?, ?, ?)",
                (url, status, time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            pass


_session_lock = threading.Lock()
_session_id: Optional[str] = None
_session_cookies: Dict[str, str] = {}
_session_ua: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _create_session() -> requests.Session:
    """FlareSolverr에서 받은 쿠키/UA로 세션 생성."""
    session = requests.Session()
    with _session_lock:
        cookies = dict(_session_cookies)
        ua = _session_ua
    session.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    })
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".bookto31.com")
    return session


def _flaresolverr_request(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Dict:
    """FlareSolverr로 URL 요청 → solution dict 반환."""
    payload = {"cmd": "request.get", "url": url, "maxTimeout": timeout_ms}
    with _session_lock:
        if _session_id:
            payload["session"] = _session_id
    resp = requests.post(
        FLARESOLVERR_URL,
        json=payload,
        timeout=(timeout_ms / 1000) + 30,
    )
    resp.raise_for_status()
    data = resp.json()
    sol = data.get("solution") or {}
    return sol


def _update_session_state(sol: Dict) -> bool:
    """FlareSolverr 응답에서 쿠키/UA 추출 후 캐시 갱신. 성공 시 True."""
    if sol.get("status") != 200:
        return False
    cookies = sol.get("cookies") or []
    ua = sol.get("userAgent") or _session_ua
    new_cookies: Dict[str, str] = {}
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        if name and value:
            new_cookies[name] = value
    if not new_cookies:
        return False
    with _session_lock:
        _session_cookies.clear()
        _session_cookies.update(new_cookies)
        _session_ua = ua
    return True


def _fetch_with_flaresolverr(url: str, max_attempts: int = 3, rate_limit: bool = True) -> Optional[str]:
    """FlareSolverr 통해 URL의 HTML 본문을 가져온다. None이면 실패.

    rate_limit=True (기본): 8분 간격 + ±2분 jitter로 같은 URL 도배 방지.
    rate_limit=False: 챕터 일괄 수집 시 이미 제한된 상태에서 호출.
    """
    if rate_limit:
        _rate_limit_check(url)
    for attempt in range(max_attempts):
        try:
            sol = _flaresolverr_request(url)
        except Exception as e:
            time.sleep(1)
            continue
        if sol.get("status") == 200:
            _update_session_state(sol)
            if rate_limit:
                _rate_limit_record(url, status=200)
            return sol.get("response") or ""
        # challenge 해결 실패시 재시도
        time.sleep(2)
    if rate_limit:
        _rate_limit_record(url, status=403)
    return None


def fetch_home() -> Optional[str]:
    """북토끼 홈 페이지."""
    return _fetch_with_flaresolverr(f"{BASE_URL}/")


def fetch_search(query: str) -> Optional[str]:
    """검색 결과 페이지 HTML."""
    import urllib.parse
    q = urllib.parse.quote(query)
    return _fetch_with_flaresolverr(f"{BASE_URL}/bbs/search.php?stx={q}")


def fetch_novel_index(novel_id: int) -> Optional[str]:
    """개별 소설(작품) 페이지 - 회차 목록 포함.

    GNUBOARD5 URL: /bbs/board.php?bo_table=novel&wr_id={novel_id}
    여기서 novel_id는 wr_id (작품 페이지 ID).
    """
    return _fetch_with_flaresolverr(
        f"{BASE_URL}/bbs/board.php?bo_table=novel&wr_id={novel_id}"
    )


def fetch_chapter(wr_id: int) -> Optional[str]:
    """회차 본문 페이지 HTML.

    URL: /bbs/board.php?bo_table=novel&wr_id={wr_id}
    wr_id는 회차(에피소드)의 ID. 회차 목록 페이지에서 추출 가능.
    """
    return _fetch_with_flaresolverr(
        f"{BASE_URL}/bbs/board.php?bo_table=novel&wr_id={wr_id}"
    )


def parse_chapter_list(html: str, novel_id: int) -> List[Dict]:
    """작품 페이지 HTML에서 회차(wr_id, 제목) 목록 추출.

    북토끼/APMS 회차 링크는 item-subject 안:
    - <a href="...board.php?bo_table=novel&wr_id={wr_id}..." class="item-subject">
        ...하남자의 탑 공략법 - 557화...<span class="count ...">N</span></a>
    """
    import re
    pattern = (
        r'<a\s+href="[^"]*?bo_table=novel(?:&amp;|&)wr_id=(\d+)[^"]*"'
        r'[^>]*class="item-subject"[^>]*>(.*?)</a>'
    )
    matches = re.findall(pattern, html, re.DOTALL)
    seen = set()
    out: List[Dict] = []
    for wr_id, inner in matches:
        wr_id_int = int(wr_id)
        if wr_id_int == novel_id or wr_id_int in seen:
            continue
        seen.add(wr_id_int)
        title = re.sub(r"<[^>]+>", " ", inner)
        title = re.sub(r"\s+", " ", title).strip()
        out.append({"wr_id": wr_id_int, "title": title})
    return out


def parse_chapter_body(html: str) -> str:
    """회차 본문 HTML에서 본문 텍스트 추출.

    북토끼/APMS는 <div class="view-content book-text-viewer"> 안에 본문이 들어있음.
    """
    import re
    m = re.search(
        r'<div[^>]*class="view-content book-text-viewer"[^>]*>(.*?)</div>',
        html,
        re.DOTALL,
    )
    if not m:
        return ""
    section = m.group(1)
    clean = re.sub(r"<script[^>]*>.*?</script>", "", section, flags=re.DOTALL)
    clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<[^>]+>", "\n", clean)
    clean = re.sub(r"\n\s*\n", "\n", clean)
    return clean.strip()


def parse_novel_meta(html: str) -> Dict:
    """작품 페이지에서 메타데이터 추출 (제목, 작가, 설명 등)."""
    import re
    title_m = re.search(r"<title>(.*?)</title>", html)
    desc_m = re.search(
        r'<meta property="og:description" content="([^"]+)"', html
    )
    author_m = re.search(
        r'<meta name="author" content="([^"]+)"', html
    )
    return {
        "title": title_m.group(1) if title_m else "",
        "description": (
            desc_m.group(1).replace("&#039;", "'").replace("&quot;", '"')
            if desc_m else ""
        ),
        "author": author_m.group(1) if author_m else "",
    }