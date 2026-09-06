#!/usr/bin/env python3
# Status: refactored (Phase 2)
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

리팩터링 (2026-09-06):
- 내부 FlareSolverr 세션 관리 → lib.flaresolverr_client.FlareSolverrSession 사용
- inline rate limiter → lib.rate_limiter 사용 (FlareSolverrSession 내장)
"""

from typing import Optional, List, Dict, Tuple

from lib.flaresolverr_client import FlareSolverrSession


BASE_URL = "https://bookto31.com"

# bookto31 전용 FlareSolverr 세션 (rate_limit=True: 8분 간격)
_fs = FlareSolverrSession(rate_limit=True)


def _fetch_with_flaresolverr(url: str, max_attempts: int = 3, rate_limit: bool = True) -> Optional[str]:
    """FlareSolverr 통해 URL의 HTML 본문을 가져온다. None이면 실패.

    rate_limit=True (기본): 8분 간격 + ±2분 jitter로 같은 URL 도배 방지.
    rate_limit=False: 챕터 일괄 수집 시 이미 제한된 상태에서 호출.

    Note: 외부 모듈(ebook_worker.py 등)에서 아직 직접 호출할 수 있어 유지.
    """
    if rate_limit:
        return _fs.fetch(url, max_attempts=max_attempts)
    # rate_limit=False: 임시 세션으로 호출
    no_limit_fs = FlareSolverrSession(rate_limit=False)
    return no_limit_fs.fetch(url, max_attempts=max_attempts)


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

    북토끼/APMS 회차 nav는 item-subject 클래스 안에:
    - <a href="...wr_id=N..." class="item-subject">
    -     <span class="orangered">...</span>
    -     오늘만 사는 기사 - 839화
    -     <span class="count">N</span>
    -   </a>

    Returns:
        [(wr_id, chapter_number), ...]
    """
    import re

    pattern = re.compile(
        r'<a[^>]*?(?:class="item-subject"[^>]*?)?'
        r'href="[^"]*(?:&amp;)?wr_id=(\d+)[^"]*"'
        r'[^>]*?(?:class="item-subject"[^>]*?)?>(.*?)</a>',
        re.DOTALL,
    )
    matches = []
    for m in pattern.finditer(html):
        wr_id = int(m.group(1))
        inner = m.group(2)
        text = re.sub(r'<[^>]+>', ' ', inner)
        text = re.sub(r'\s+', ' ', text).strip()
        ep_match = re.search(r'(\d+)(?:화|편|장)', text)
        if ep_match:
            chapter = int(ep_match.group(1))
            matches.append((wr_id, chapter))

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
