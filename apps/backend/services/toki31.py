#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/toki31.py
"""toki31.com 크롤러 - 일반 PC 브라우저 헤더 적용"""

import re
import requests
from typing import Optional


BASE_URL = "https://toki31.com"


def _build_headers() -> dict:
    """일반 Windows Chrome 브라우저처럼 보이게 만드는 헤더"""
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


def _create_session() -> requests.Session:
    """재사용 가능한 Session 객체 생성 (쿠키/연결 유지)"""
    session = requests.Session()
    session.headers.update(_build_headers())
    return session


def fetch_novel_list(page: int = 1) -> Optional[str]:
    """toki31 소설 목록 페이지 HTML을 가져온다.

    Note:
        toki31은 ASN 단위(Oracle Cloud 등)로 IP 차단을 적용할 수 있어
        헤더만으로는 우회가 안 될 수 있다. 차단을 피하려면
        일반 residential 네트워크를 통해 요청해야 한다.
    """
    session = _create_session()
    url = f"{BASE_URL}/novel/list?page={page}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def fetch_novel_detail(novel_id: int) -> Optional[str]:
    """개별 소설 상세 페이지 HTML을 가져온다."""
    session = _create_session()
    url = f"{BASE_URL}/novel/{novel_id}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def fetch_chapter(novel_id: int, chapter_id: int) -> Optional[str]:
    """소설 본문(회차) HTML을 가져온다."""
    session = _create_session()
    url = f"{BASE_URL}/novel/{novel_id}/{chapter_id}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text