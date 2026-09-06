#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/user_agent.py
"""Chrome 브라우저 헤더 빌더 — bookto31/toki31 공용.

bookto31.py의 _create_session() inline 헤더와
toki31.py의 _build_headers()를 통합.
"""

from typing import Optional


# Chrome 버전별 UA 매핑
_UA_MAP = {
    "120": "Chrome/120.0.0.0",
    "124": "Chrome/124.0.0.0",
    "126": "Chrome/126.0.0.0",
    "131": "Chrome/131.0.0.0",
}


def chrome_headers(version: str = "131") -> dict:
    """Chrome 브라우저 헤더 반환.

    Args:
        version: "120" | "124" | "126" | "131" (기본)

    Returns:
        Accept, Accept-Language, User-Agent, Sec-CH-UA 등 완전한 헤더 dict
    """
    ua_version = _UA_MAP.get(version, _UA_MAP["131"])
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) {ua_version} Safari/537.36"
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
        "Sec-Ch-Ua": f'"Not_A Brand";v="8", "Chromium";v="{version}", "Google Chrome";v="{version}"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }


def namu_headers() -> dict:
    """namu.wiki용 헤더 (Referer 포함)."""
    headers = chrome_headers("131")
    headers["Referer"] = "https://namu.wiki/"
    return headers
