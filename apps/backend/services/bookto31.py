#!/usr/bin/env python3
# Status: refactored (Phase 2)
# Path: ebooklib/apps/backend/services/bookto31.py
"""23.ondobook.net (북토끼) 크롤러 - FlareSolverr Cloudflare 우회 + GNUBOARD5 파싱

배경:
- 23.ondobook.net은 Cloudflare Turnstile Challenge로 보호된다.
- 자동화 도구(requests, curl)는 403을 받고 쿠키/세션이 없으면 본문이 비어있다.
- FlareSolverr (http://127.0.0.1:8191)을 통해 헤드리스 브라우저로 challenge를 풀고
  cf_clearance 쿠키를 받은 뒤 페이지를 가져온다.
- 본문 사이트는 GNUBOARD5 + APMS 테마. 회차 목록은 `<div class="view-content book-list">`,
  본문은 `<div class="view-content book-text-viewer">`.

FlareSolverr 응답 구조 (FlareSolverr v3.x):
- cmd=request.get → solution: {status, url, cookies: [...], response: <HTML>, userAgent}
- cookies는 [{domain, name, value, ...}, ...] 형태. request.headers["Cookie"]로 전달 가능.

리팩터링 (2026-09-06):
- 내부 FlareSolverr 세션 관리 → lib.flaresolverr_client.FlareSolverrSession 사용
- inline rate limiter → lib.rate_limiter 사용 (FlareSolverrSession 내장)
"""

from typing import Optional, List, Dict, Tuple

from lib.flaresolverr_client import FlareSolverrSession
from lib.sources import get_base_url
from lib.domain_router import candidate_bases


# bookto31 전용 FlareSolverr 세션 (rate_limit=True: 8분 간격)
_fs = FlareSolverrSession(rate_limit=True)

# 리다이렉트 감지/페일오버 시도용 짧은 타임아웃 (도메인 사망 시 빠른 전환)
_PROBE_TIMEOUT_MS = 20000
_DEFAULT_TIMEOUT_MS = 60000


def _fetch_with_flaresolverr(
    url: str,
    max_attempts: int = 3,
    rate_limit: bool = True,
    source: Optional[str] = None,
    timeout_ms: Optional[int] = None,
) -> Optional[str]:
    """FlareSolverr 통해 URL의 HTML 본문을 가져온다. None이면 실패.

    rate_limit=True (기본): 8분 간격 + ±2분 jitter로 같은 URL 도배 방지.
    rate_limit=False: 챕터 일괄 수집/헬스체크 시 이미 제한된 상태에서 호출.
    source가 주어지면 리다이렉트 최종 도메인으로 base_url 자동 갱신.

    Note: 외부 모듈(ebook_worker.py 등)에서 아직 직접 호출할 수 있어 유지.
    """
    timeout = timeout_ms if timeout_ms is not None else _DEFAULT_TIMEOUT_MS
    if rate_limit:
        return _fs.fetch(
            url, max_attempts=max_attempts, source=source, timeout_ms=timeout
        )
    # rate_limit=False: 임시 세션으로 호출
    no_limit_fs = FlareSolverrSession(rate_limit=False)
    return no_limit_fs.fetch(
        url, max_attempts=max_attempts, source=source, timeout_ms=timeout
    )


def _site_url(source: str = "bookto31") -> str:
    """소스별 크롤링 베이스 URL (bookto31/newto31 등 gnuboard 계열 공용)."""
    return get_base_url(source)


def _board_url(source: str, bo_table: str, wr_id: int) -> str:
    """gnuboard 회차/작품 페이지 URL."""
    return f"{_site_url(source)}/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}"


def _fetch_page(
    source: str,
    path: str,
    max_attempts: int = 3,
    timeout_ms: Optional[int] = None,
) -> Optional[str]:
    """후보 도메인(base_url + 미러) 순회 fetch — 첫 성공 HTML 반환.

    현재 도메인이 죽으면 다음 후보(domains 목록)로 자동 전환하고,
    리다이렉트로 실제 도메인이 바뀌면 sources.json base_url을 자동 갱신한다.
    """
    for base in candidate_bases(source):
        url = f"{base.rstrip('/')}{path}"
        html = _fetch_with_flaresolverr(
            url,
            max_attempts=max_attempts,
            source=source,
            timeout_ms=timeout_ms,
        )
        if html is not None:
            return html
    return None


def _flaresolverr_alive(timeout: float = 5.0) -> bool:
    """FlareSolverr API 자체가 응답하는지 확인 (프로브 인프라 생존 여부)."""
    import requests
    try:
        r = requests.post(
            "http://127.0.0.1:8191/v1",
            json={"cmd": "sessions.list"},
            timeout=timeout,
        )
        return r.status_code == 200
    except Exception:
        return False


def probe_base(base: str, timeout_ms: int = _PROBE_TIMEOUT_MS) -> Optional[bool]:
    """단일 베이스 URL이 살아있는지 홈 페이지 응답으로 확인 (헬스체크용).

    rate limit 미적용 + 짧은 타임아웃. FlareSolverr 헤드리스로 챌린지 해결 후
    HTML이 돌아오면 True.

    Returns:
        True  — 사이트 응답 정상
        False — 사이트 응답 불가
        None  — 판별 불가 (FlareSolverr 자체가 죽어 있어 프로브 불가)
    """
    if not _flaresolverr_alive():
        return None
    url = f"{base.rstrip('/')}/"
    html = _fetch_with_flaresolverr(
        url, max_attempts=1, rate_limit=False, timeout_ms=timeout_ms
    )
    return html is not None


def fetch_home(source: str = "bookto31") -> Optional[str]:
    """홈 페이지 (소스별 도메인)."""
    return _fetch_page(source, "/")


def fetch_search(query: str, source: str = "bookto31") -> Optional[str]:
    """검색 결과 페이지 HTML."""
    import urllib.parse
    q = urllib.parse.quote(query)
    return _fetch_page(source, f"/bbs/search.php?stx={q}")


def fetch_novel_index(novel_id: int, source: str = "bookto31", bo_table: str = "novel") -> Optional[str]:
    """개별 소설(작품) 페이지 - 회차 목록 포함.

    GNUBOARD5 URL: /bbs/board.php?bo_table={bo_table}&wr_id={novel_id}
    여기서 novel_id는 wr_id (작품 페이지 ID). bo_table은 게시판(콘텐츠 종류).
    """
    return _fetch_page(source, f"/bbs/board.php?bo_table={bo_table}&wr_id={novel_id}")


def fetch_chapter(wr_id: int, source: str = "bookto31", bo_table: str = "novel") -> Optional[str]:
    """회차 본문 페이지 HTML.

    URL: /bbs/board.php?bo_table={bo_table}&wr_id={wr_id}
    wr_id는 회차(에피소드)의 ID. bo_table은 게시판(콘텐츠 종류).
    """
    return _fetch_page(source, f"/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}")


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


def extract_webtoon_images(html: str) -> List[str]:
    """웹툰 회차 페이지에서 콘텐츠 이미지 URL을 순서대로 추출.

    newto31 등 gnuboard 웹툰 게시판의 본문 이미지는 업로드 CDN
    (imgspeedtoki 계열)의 <img> (0001_, 0002_, ... 순차)로 제공된다.
    광고/배너/로고 등은 제외하고 콘텐츠 CDN 이미지만 수집한다.
    """
    import re

    urls = []
    for m in re.finditer(r"<img[^>]+>", html):
        tag = m.group(0)
        src_m = re.search(r'src="([^"]+)"', tag)
        if not src_m:
            continue
        url = src_m.group(1)
        # 콘텐츠 업로드 CDN만 (imgspeedtoki 계열) — newto31.com 로고/배너 제외
        if re.search(r"https?://[^/\s]*imgspeedtoki\d*\.com", url, re.IGNORECASE):
            urls.append(url)
    # 순서 유지 + 중복 제거
    seen = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_chapter_body(html: str) -> str:
    """회차 본문 HTML에서 본문 텍스트 추출.

    북토끼/APMS는 <div class="view-content book-text-viewer"> 안에 본문이 들어있음.
    중첩 div 안전 추출(depth 추적) + 불필요 요소 제거로 본문만 남긴다.
    """
    import html as html_lib
    import re

    section = _extract_book_text_viewer(html)
    if section is None:
        return ""

    # 불필요 요소 제거 (스크립트/스타일/iframe/폼/광고)
    section = re.sub(r"<script[^>]*>.*?</script>", "", section, flags=re.DOTALL | re.IGNORECASE)
    section = re.sub(r"<style[^>]*>.*?</style>", "", section, flags=re.DOTALL | re.IGNORECASE)
    section = re.sub(r"<iframe[^>]*>.*?</iframe>", "", section, flags=re.DOTALL | re.IGNORECASE)
    section = re.sub(r"<noscript[^>]*>.*?</noscript>", "", section, flags=re.DOTALL | re.IGNORECASE)
    section = re.sub(r"<form[^>]*>.*?</form>", "", section, flags=re.DOTALL | re.IGNORECASE)
    # 광고/감지 div (클래스명 기반)
    section = re.sub(
        r'<div[^>]*class="[^"]*(?:ad|banner|popup|modal|toast|notice|footer|header)[^"]*"[^>]*>.*?</div>',
        "", section, flags=re.DOTALL | re.IGNORECASE)

    # 태그 → 개행, 엔티티 → 문자
    clean = re.sub(r"<br\s*/?>", "\n", section, flags=re.IGNORECASE)
    clean = re.sub(r"<p[^>]*>", "\n", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = html_lib.unescape(clean)
    # 잔여 공백/개행 정리
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    clean = re.sub(r"^\s+|\s+$", "", clean)
    return clean.strip()


def _extract_book_text_viewer(html: str) -> Optional[str]:
    """본문 영역(<div class="view-content ...">)을 중첩 div 안전 추출.

    사이트별 클래스 변형을 지원:
    - bookto31: "view-content book-text-viewer"
    - ondobook(23.ondobook.net): "view-content" (단독)
    우선순위: book-text-viewer 포함 → 일반 view-content 순.

    단순 .*?</div>는 중첩 div가 있으면 첫 </div>에서 잘린다.
    태그 파서로 div depth를 추적해 본문 div의 전체 내부를 반환한다.

    Returns:
        본문 내부 HTML 또는 None
    """
    import re

    # 본문 시작 div 탐색 (1) book-text-viewer 포함, (2) 일반 view-content
    start_m = None
    for pattern in (
        r'<div[^>]*class="[^"]*view-content[^"]*book-text-viewer[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*view-content[^"]*"[^>]*>',
    ):
        start_m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if start_m:
            break
    if not start_m:
        return None
    start = start_m.end()

    # 시작 지점부터 div depth 추적
    depth = 1
    i = start
    n = len(html)
    while i < n and depth > 0:
        tag_m = re.search(r"</?[a-zA-Z][^>]*>", html[i:], re.DOTALL)
        if not tag_m:
            break
        tag = tag_m.group(0)
        pos = i + tag_m.start()
        self_closing = tag.rstrip().endswith("/>")
        inner = tag[1:].lstrip()
        m = re.match(r"(/?)([a-zA-Z0-9]+)", inner)
        if m and not self_closing:
            closing = bool(m.group(1))
            tname = m.group(2).lower()
            if tname == "div":
                depth += -1 if closing else 1
        i = pos + len(tag)
        if depth <= 0:
            return html[start:pos]

    # 끝까지 갔으면 start~끝 반환
    return html[start:] if start < n else None


def is_novel_index_page(html: str) -> bool:
    """HTML이 작품 메인 페이지인지 판단.

    작품 메인: 회차 목록만 있고 본문이 짧음 (< 1000자)
    회차 페이지: view-content book-text-viewer 또는 긴 본문
    """
    if "view-content book-text-viewer" in html:
        return False
    body = parse_chapter_body(html)
    if body and len(body) > 500:
        return False
    return True


def extract_chapter_wr_ids_from_index(html: str) -> List[Tuple[int, int]]:
    """작품 메인 페이지에서 (wr_id, chapter_num) 추출.

    북토끼/APMS 회차 nav는 두 가지 구조가 있음:
    1. select 드롭다운 (현재 사이트 기본):
       <select name="wr_id" class="toon-episode-select">
         <option value="58449">271화</option>
       </select>
    2. item-subject 링크 (구버전):
       <a href="...wr_id=N..." class="item-subject">... N화 ...</a>

    Returns:
        [(wr_id, chapter_number), ...]
    """
    import re

    matches = []

    # 패턴 1: select > option 드롭다운
    for m in re.finditer(
        r'<option[^>]*value="(\d+)"[^>]*>\s*(\d+)\s*(?:화|편|장)\s*</option>',
        html,
    ):
        matches.append((int(m.group(1)), int(m.group(2))))

    # 패턴 2: item-subject 링크 (옵션이 없을 때만 사용)
    if not matches:
        pattern = re.compile(
            r'<a[^>]*?(?:class="item-subject"[^>]*?)?'
            r'href="[^"]*(?:&amp;)?wr_id=(\d+)[^"]*"'
            r'[^>]*?(?:class="item-subject"[^>]*?)?>(.*?)</a>',
            re.DOTALL,
        )
        for m in pattern.finditer(html):
            wr_id = int(m.group(1))
            inner = m.group(2)
            text = re.sub(r'<[^>]+>', ' ', inner)
            text = re.sub(r'\s+', ' ', text).strip()
            ep_match = re.search(r'(\d+)(?:화|편|장)', text)
            if ep_match:
                matches.append((wr_id, int(ep_match.group(1))))

    seen = set()
    unique = []
    for wr_id, ch in matches:
        if wr_id not in seen:
            seen.add(wr_id)
            unique.append((wr_id, ch))
    return unique


def find_chapter_wr_id(html: str, novel_main_wr_id: int, chapter_num: int) -> Optional[int]:
    """작품 메인 페이지에서 특정 회차 번호의 wr_id를 찾는다.

    Args:
        html: 작품 메인 페이지 HTML
        novel_main_wr_id: 작품 메인 페이지의 wr_id (제외용)
        chapter_num: 찾을 회차 번호

    Returns:
        회차 wr_id 또는 None
    """
    import re

    pattern = re.compile(
        r'<a[^>]*?(?:class="item-subject"[^>]*?)?'
        r'href="[^"]*(?:&amp;)?wr_id=(\d+)[^"]*"'
        r'[^>]*?(?:class="item-subject"[^>]*?)?>(.*?)</a>',
        re.DOTALL,
    )
    for m in pattern.finditer(html):
        wr_id = int(m.group(1))
        if wr_id == novel_main_wr_id:
            continue
        inner = m.group(2)
        text = re.sub(r'<[^>]+>', ' ', inner)
        text = re.sub(r'\s+', ' ', text).strip()
        ep_match = re.search(rf'{chapter_num}\s*(?:화|편|장)', text)
        if ep_match:
            return wr_id

    return None


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
