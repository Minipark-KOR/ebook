#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/metadata_namu.py
"""namu.wiki 메타데이터 검색.

namu.wiki는 한국어 웹소설/웹툰의 메타데이터를 잘 정리해둔 한국 최대 위키.
- 표지 이미지, 작가, 장르, 연재 상태, 첫화 날짜 등 풍부
- robots.txt: /w/ 경로 허용 → 크롤링 가능
- 단, namu.wiki 이용약관: 메타데이터 인용은 OK, 본문 전체 복제 금지

남용 방지: rate_limit 30분 + ±5분 jitter
"""

import json
import re
import time
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from lib.flaresolverr_client import FlareSolverrSession


# namu.wiki 전용 FlareSolverr 세션 (rate_limit=False: namu.wiki 자체 30분 제한 사용)
_namu_fs = FlareSolverrSession(rate_limit=False)


# namu.wiki 전용 rate limiter
_NAMU_DB_PATH = Path("/opt/ai_data/flaresolverr/namu_rate_limiter.db")
_NAMU_INTERVAL = 1800  # 30분
_NAMU_JITTER = 300  # ±5분

_namu_lock = threading.Lock()


def _namu_rate_limit() -> None:
    """namu.wiki 호출 전 rate limit 적용."""
    try:
        from lib.rate_limiter import wait_if_needed
        wait_if_needed(
            "https://namu.wiki",
            interval=_NAMU_INTERVAL,
            db_path=_NAMU_DB_PATH,
        )
    except ImportError:
        # 폴백: 인라인 rate limit
        import sqlite3
        import random
        _NAMU_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_NAMU_DB_PATH))
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
            ("https://namu.wiki",),
        ).fetchone()
        if row:
            elapsed = time.time() - row[0]
            if elapsed < _NAMU_INTERVAL:
                wait = (_NAMU_INTERVAL - elapsed) + random.uniform(0, _NAMU_JITTER)
                time.sleep(wait)
        conn.close()


def _namu_record() -> None:
    """namu.wiki 호출 기록."""
    try:
        from lib.rate_limiter import record_request
        record_request("https://namu.wiki", status=200, db_path=_NAMU_DB_PATH)
    except ImportError:
        pass


@dataclass
class NamuMetadata:
    """namu.wiki에서 가져온 메타데이터"""
    title: str = ""
    author: str = ""
    description: str = ""
    cover_url: Optional[str] = None
    status: str = "unknown"  # 연재중/완결/단편
    genre: list[str] = None
    publisher: str = "북토끼"
    first_published: str = ""
    rating: str = ""
    source: str = "namu.wiki"
    url: str = ""

    def __post_init__(self):
        if self.genre is None:
            self.genre = []


def _make_headers() -> dict:
    """namu.wiki용 헤더 (일반 브라우저처럼)."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://namu.wiki/",
        "DNT": "1",
    }


def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """namu.wiki 페이지 fetch (FlareSolverr 사용, Cloudflare 우회).

    namu.wiki가 Cloudflare Turnstile/봇 탐지를 적용하므로
    FlareSolverr 헤드리스 브라우저로 우회.

    Note: FlareSolverrSession(rate_limit=False) 사용.
    namu.wiki는 자체 30분 rate limiter(_namu_rate_limit)가 별도로 있음.
    """
    html = _namu_fs.fetch(url)
    if html:
        _namu_record()
    return html or None


def _fetch_binary(url: str, timeout: int = 30) -> Optional[bytes]:
    """namu.wiki 이미지 다운로드 (FlareSolverr 우회).

    Returns:
        이미지 바이트 또는 None
    """
    # 1순위: FlareSolverr (Cloudflare 우회)
    html_text = _namu_fs.fetch(url)
    if html_text:
        raw = html_text.encode('utf-8') if isinstance(html_text, str) else html_text
        if raw.startswith(b'\x89PNG') or raw.startswith(b'\xff\xd8\xff') or \
           raw.startswith(b'RIFF') or raw.startswith(b'<?xml'):
            _namu_record()
            return raw

    # 2순위: requests (간단 헤더)
    try:
        headers = _make_headers()
        if 'i.namu.wiki' in url:
            headers['Referer'] = 'https://namu.wiki/'
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
        _namu_record()
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def download_cover(cover_url: str, save_path: Path, timeout: int = 30) -> bool:
    """표지 이미지 다운로드 후 로컬에 저장.

    Args:
        cover_url: namu.wiki 표지 URL
        save_path: 저장할 로컬 경로 (.webp, .jpg 등)
        timeout: HTTP 타임아웃

    Returns:
        성공 시 True, 실패 시 False
    """
    if not cover_url:
        return False

    # URL 정규화
    if cover_url.startswith("//"):
        cover_url = "https:" + cover_url

    binary = _fetch_binary(cover_url, timeout)
    if not binary:
        return False

    # 최소 크기 체크 (1KB 이상)
    if len(binary) < 1024:
        return False

    # 확장자 결정 (Content-Type 또는 URL)
    ext = ".webp"
    if cover_url.lower().endswith(".jpg") or cover_url.lower().endswith(".jpeg"):
        ext = ".jpg"
    elif cover_url.lower().endswith(".png"):
        ext = ".png"

    # 저장 (확장자 일치)
    if not save_path.suffix:
        save_path = save_path.with_suffix(ext)

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'wb') as f:
        f.write(binary)

    return True


def _extract_metadata_from_html(html: str) -> NamuMetadata:
    """namu.wiki HTML에서 메타데이터 추출."""
    meta = NamuMetadata()

    # 1. <title>
    title_m = re.search(r'<title>(.*?)\s*-\s*나무위키</title>', html)
    if title_m:
        meta.title = title_m.group(1).strip()

    # 2. OG 메타 태그
    og_title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if og_title:
        meta.title = og_title.group(1).replace(" - 나무위키", "").strip()

    og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if og_desc:
        meta.description = og_desc.group(1).strip()

    og_image = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if og_image:
        # //i.namu.wiki/... → https:// 추가
        url = og_image.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        meta.cover_url = url

    # 3. 본문에서 추출
    # 카테고리 (장르)
    classification = re.search(
        r'<ul[^>]*class="[^"]*classification[^"]*"[^>]*>(.*?)</ul>',
        html, re.DOTALL,
    )
    if classification:
        genres = re.findall(r'<li[^>]*>(.*?)</li>', classification.group(1), re.DOTALL)
        meta.genre = [re.sub(r'<[^>]+>', '', g).strip() for g in genres if g.strip()][:5]

    # 4. namu.wiki 메타 정보 추출 (Vuetify 기반 구조)
    # 패턴 1: <div ...>label</div>...<a ...>value</a>
    # 패턴 2: <th>label</th><td>value</td>
    # 패턴 3: inline: <span>label</span> <a>value</a>

    # 방법 1: 강력한 정규식 - "label ... value" 패턴
    # namu.wiki 메타 정보 박스 구조: <div class="..."> <div>라벨</div> <a href="...">값</a> </div>
    meta_pairs = re.findall(
        r'class="[^"]*_f2e45c8c47fa444a1b8d9d005ec3b518[^"]*"[^>]*?>(.*?)</a>',
        html, re.DOTALL,
    )
    # 각 매치에서 라벨/값 추출
    for inner in meta_pairs:
        # 라벨 부분: <div>라벨명</div>
        label_m = re.search(r'<div[^>]*>([^<]+)</div>', inner)
        if not label_m:
            continue
        label = re.sub(r'\s+', ' ', label_m.group(1)).strip()

        # 값 부분: <a ...>값</a> 또는 텍스트
        value_m = re.search(r'<a[^>]*>([^<]+)</a>', inner)
        if not value_m:
            value_m = re.search(r'<div[^>]*>([^<]+)</div>$', inner)
        if not value_m:
            continue
        value = re.sub(r'<[^>]+>', '', value_m.group(1)).strip()
        value = re.sub(r'\s+', ' ', value)

        if not value or len(value) > 200:
            continue

        if '작가' in label or '글쓴이' in label or '원작' in label or '저자' in label or '집필' in label:
            meta.author = value
        elif '장르' in label and not meta.genre:
            meta.genre = [value]
        elif '연재' in label or '완결' in value or '연재중' in value or '단편' in value:
            if '완결' in value:
                meta.status = '완결'
            elif '연재' in value or '연재중' in value:
                meta.status = '연재중'
            elif '단편' in value:
                meta.status = '단편'
        elif '연재처' in label or '출판사' in label or '발행사' in label or '출판' in label:
            meta.publisher = value

    # 방법 2: og:description에서 작가/장르 추출 (백업)
    if not meta.author and meta.description:
        # "작가는 XXX" 패턴 - 단, 다른 책 제목이 잡힐 수 있어 신중하게
        # namu.wiki는 작가 항목이 별도로 있으면 그게 우선 (위에서 추출됨)
        # og:description의 "작가는 X"는 부정확할 수 있어 추출 안 함
        # 사용자가 북토끼/문피아에서 직접 확인 권장
        pass

    if not meta.genre and meta.description:
        # "한국의 XXX 웹소설" 패턴
        genre_m = re.search(r'한국의\s+([^,]+?)\s+웹소설', meta.description)
        if genre_m:
            meta.genre = [g.strip() for g in genre_m.group(1).split(',') if g.strip()][:3]

    return meta


def search_novel(title: str, timeout: int = 15) -> Optional[NamuMetadata]:
    """namu.wiki에서 소설 메타데이터 검색.

    Args:
        title: 소설 제목 (예: "하남자의 탑 공략법")
        timeout: HTTP 타임아웃

    Returns:
        NamuMetadata 또는 None
    """
    with _namu_lock:
        _namu_rate_limit()

    # namu.wiki URL 인코딩 (공백 → %20)
    url_title = quote(title, safe="")
    url = f"https://namu.wiki/w/{url_title}"

    html = _fetch_url(url, timeout)
    if not html:
        return None

    # 본문 길이 체크 (너무 짧으면 검색 결과 페이지)
    if len(html) < 5000:
        return None

    # "검색 결과" 페이지인지 확인 (실제 문서 페이지가 아닌 경우)
    if "검색결과" in html and "searchResult" in html:
        return None

    meta = _extract_metadata_from_html(html)
    meta.url = url
    return meta


def search_novel_fallback(title: str) -> Optional[NamuMetadata]:
    """namu.wiki 검색 결과에서 가장 관련도 높은 항목 찾기.

    namu.wiki에 정확한 제목이 없을 때 사용.
    """
    with _namu_lock:
        _namu_rate_limit()

    search_url = f"https://namu.wiki/Search?query={quote(title)}"
    html = _fetch_url(search_url, 15)
    if not html:
        return None

    # 검색 결과 링크 추출
    links = re.findall(r'href="(/w/[^"]+)"[^>]*class="[^"]*title[^"]*"', html)
    if not links:
        # 더 넓은 매칭
        all_links = re.findall(r'href="(/w/[^"]+)"', html)
        # 검색어와 비슷한 것만
        for link in all_links:
            decoded = link.replace("/w/", "").replace("+", " ")
            if any(word in decoded for word in title.split() if len(word) > 1):
                return search_novel(decoded)
        return None

    if not links:
        return None

    # 첫 번째 결과로 fetch
    first_link = links[0]
    title_guess = first_link.replace("/w/", "")
    return search_novel(title_guess)


def get_metadata(title: str, download_cover_to: Optional[Path] = None) -> Optional[dict]:
    """공개 API: NamuMetadata → dict (services.metadata와 호환).

    Args:
        title: 소설 제목
        download_cover_to: 표지 이미지 저장 경로 (None이면 URL만)

    Returns:
        dict with title, author, cover_url, description, etc.
        또는 None
    """
    meta = search_novel(title)
    if not meta:
        # 폴백: 검색
        meta = search_novel_fallback(title)
    if not meta:
        return None

    cover_url = meta.cover_url

    # 표지 이미지를 로컬에 다운로드 (옵션)
    if cover_url and download_cover_to is not None:
        if download_cover(
            cover_url, download_cover_to, timeout=30
        ):
            # DB에는 로컬 경로 저장 (백엔드 static mount)
            cover_url = f"/api/covers/{download_cover_to.name}"
    elif cover_url:
        # Vercel Image Optimization 또는 자체 프록시로 안정적 제공
        # namu.wiki URL → 백엔드 프록시 URL
        cover_url = f"/api/novels/image-proxy?url={quote(cover_url, safe='')}"

    return {
        "title": meta.title,
        "author": meta.author,
        "description": meta.description,
        "cover_url": cover_url,
        "status": meta.status,
        "genre": meta.genre,
        "publisher": meta.publisher,
        "first_published": meta.first_published,
        "source": meta.source,
        "url": meta.url,
    }