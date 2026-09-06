#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/curl_session.py
"""curl_cffi 세션 팩토리 — TLS fingerprint 위장.

toki31.py의 requests + 무료 KR proxy를 대체.
curl_cffi chrome131 impersonation으로 CloudFront geo-block 우회.
"""

from typing import Optional

from curl_cffi import requests as creq

from lib.user_agent import chrome_headers


def create_curl_session(
    impersonate: str = "chrome131",
    headers: Optional[dict] = None,
    proxy: Optional[str] = None,
) -> creq.Session:
    """curl_cffi Session 생성 + 기본 헤더 설정.

    Args:
        impersonate: "chrome131" | "chrome120" | "safari17_0" | "firefox135"
                     또는 "chrome" (최신 버전 자동 선택)
        headers: 추가 헤더 (기본: Accept-Language=ko-KR 포함 Chrome 헤더)
        proxy: 프록시 URL (예: "socks5://...", "http://...")

    Returns:
        설정 완료된 curl_cffi Session

    Example:
        >>> session = create_curl_session(impersonate="chrome131")
        >>> r = session.get("https://toki31.com/", timeout=15)
        >>> print(r.status_code, len(r.text))
    """
    # impersonate에서 버전 추출 (예: "chrome131" → "131")
    version = impersonate.replace("chrome", "").replace("safari", "").replace("firefox", "")
    if not version:
        version = "131"

    session = creq.Session(impersonate=impersonate)

    # Chrome 헤더 적용
    session.headers.update(chrome_headers(version if version.isdigit() else "131"))

    # 추가 헤더
    if headers:
        session.headers.update(headers)

    # 프록시 설정
    if proxy:
        session.proxies = {"https": proxy, "http": proxy}

    return session
