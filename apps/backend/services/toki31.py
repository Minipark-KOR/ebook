#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/toki31.py
"""toki31.com (뉴토끼) 크롤러 - 일반 PC 헤더 + 무료 KR 프록시 자동 failover

배경:
- toki31은 CloudFront에서 ASN 차단(Oracle 등 datacenter) + KR_ONLY 국가 검증을 한다.
- 헤더 위장만으로는 우회 불가. residential IP(KR)가 필요하다.
- 무료 KR proxy 리스트(proxyscrape.com)를 주기적으로 가져와 동작하는 proxy만
  캐시한 뒤 자동 failover한다.

주의:
- toki31의 CloudFront geo-IP는 일부 Cloudflare IP 대역(104.28.x.x)을 CZ로 오분류한다.
  같은 residential IP여도 CloudFront로 통과 시 가끔 CZ로 감지되어 차단될 수 있다.
- 응답 본문은 JS 렌더링 전 HTML이므로 일부 동적 페이지는 SSR 결과를 못 받을 수 있다.
- 200 OK를 주는 경로(/, /novel)와 403을 주는 경로(/novel/updates, /rank)가 있다.
"""

import threading
import time
from typing import Optional, Union

import requests


BASE_URL = "https://toki31.com"

PROXY_SOURCES = [
    (
        "https://api.proxyscrape.com/v4/free-proxy-list/get"
        "?request=display_proxies&proxy_format=protocolipport"
        "&format=text&country=kr"
    ),
]

VERIFY_URL = "https://toki31.com/"  # 항상 200을 주는 루트
REFRESH_INTERVAL = 300  # 5분마다 proxy 풀 새로고침
PROXY_TIMEOUT = 10
VERIFY_TIMEOUT = 12
MAX_ATTEMPTS = 5


_proxy_lock = threading.Lock()
_proxies: list[dict] = []
_proxies_loaded_at: float = 0.0


def _build_headers() -> dict:
    """일반 Windows Chrome처럼 보이게 하는 헤더.

    주의: Accept-Encoding은 반드시 'gzip, deflate, br'이어야 한다.
    'identity'는 toki31 CloudFront에서 KR_ONLY 오분류를 일으킨다.
    """
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "DNT": "1",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def _parse_proxy_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    if "://" in line:
        scheme, rest = line.split("://", 1)
    else:
        scheme, rest = "http", line
    return {"scheme": scheme, "url": rest}


def _fetch_proxy_candidates() -> list[dict]:
    candidates: list[dict] = []
    for src in PROXY_SOURCES:
        try:
            r = requests.get(src, timeout=15)
            if r.status_code != 200:
                continue
            for line in r.text.splitlines():
                p = _parse_proxy_line(line)
                if p:
                    candidates.append(p)
        except Exception:
            continue
    return candidates


def _verify_proxy(proxy: dict) -> bool:
    """후보 proxy가 toki31에 200으로 도달하는지 검증 (루트 페이지 기준)."""
    proxies = {proxy["scheme"]: f"{proxy['scheme']}://{proxy['url']}"}
    try:
        resp = requests.get(
            VERIFY_URL,
            proxies=proxies,
            timeout=VERIFY_TIMEOUT,
            allow_redirects=False,
            headers=_build_headers(),
        )
        return resp.status_code == 200
    except Exception:
        return False


def _refresh_proxies(force: bool = False) -> None:
    """proxy 풀 검증 후 캐시 갱신."""
    global _proxies, _proxies_loaded_at
    with _proxy_lock:
        now = time.time()
        if not force and (now - _proxies_loaded_at) < REFRESH_INTERVAL and _proxies:
            return

        candidates = _fetch_proxy_candidates()
        seen: set[str] = set()
        unique: list[dict] = []
        for c in candidates:
            key = f"{c['scheme']}://{c['url']}"
            if key not in seen:
                seen.add(key)
                unique.append(c)

        verified: list[dict] = []
        for c in unique:
            if _verify_proxy(c):
                verified.append(c)
            if len(verified) >= 10:
                break

        if verified:
            _proxies = verified
            _proxies_loaded_at = now
        elif not _proxies:
            _proxies_loaded_at = now


def _create_session_with_proxy() -> Optional[requests.Session]:
    """동작하는 proxy로 세션 생성. 풀 비면 새로 검증."""
    _refresh_proxies()
    with _proxy_lock:
        if not _proxies:
            return None
        proxy = _proxies[0]
    session = requests.Session()
    session.headers.update(_build_headers())
    session.proxies = {
        proxy["scheme"]: f"{proxy['scheme']}://{proxy['url']}",
    }
    return session


def _pop_current_proxy() -> None:
    with _proxy_lock:
        if _proxies:
            _proxies.pop(0)


def _fetch_with_failover(url: str) -> Optional[str]:
    """proxy 자동 failover하면서 url 요청. 모든 proxy 실패 시 None."""
    last_err: Optional[Exception] = None
    for _ in range(MAX_ATTEMPTS):
        session = _create_session_with_proxy()
        if session is None:
            time.sleep(2)
            _refresh_proxies(force=True)
            continue
        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429, 503):
                _pop_current_proxy()
                continue
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_err = e
            _pop_current_proxy()
            continue
    if last_err:
        raise last_err
    return None


def fetch_home() -> Optional[str]:
    """toki31 홈 페이지 HTML."""
    return _fetch_with_failover(f"{BASE_URL}/")


def fetch_novel_list() -> Optional[str]:
    """toki31 소설 목록 페이지 HTML (/novel)."""
    return _fetch_with_failover(f"{BASE_URL}/novel")


def fetch_novel_detail(novel_id: Union[int, str]) -> Optional[str]:
    """개별 소설 상세 페이지 HTML."""
    return _fetch_with_failover(f"{BASE_URL}/novel/{novel_id}")


def fetch_chapter(novel_id: Union[int, str], chapter_id: Union[int, str]) -> Optional[str]:
    """소설 본문(회차) HTML."""
    return _fetch_with_failover(f"{BASE_URL}/novel/{novel_id}/{chapter_id}")