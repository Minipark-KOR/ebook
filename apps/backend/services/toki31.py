#!/usr/bin/env python3
# Status: refactored (Phase 4 + proxy + playwright)
# Path: ebooklib/apps/backend/services/toki31.py
"""toki31.com (뉴토끼) 크롤러 - curl_cffi + Playwright

배경:
- toki31은 Cloudflare에서 403 차단 (router/{id}, /rank, /search).
- curl_cffi chrome131 impersonation으로 TLS fingerprint 위장.
- 한국 주거용 프록시 (MaskProxy/DataImpulse) 필요.
- 챕터 본문은 anti-bot 보호 (ad-ack + AES-GCM 암호화)로 Playwright 필요.

리팩터링 (2026-09-06):
- requests + 무료 KR proxy → lib.curl_session (curl_cffi)
- proxy pool 관리 로직 전체 제거
- Next.js RSC payload 파서 추가
- 한국 주거용 프록시 (MaskProxy + DataImpulse) 적용
- Playwright 기반 챕터 본문 추출 (lib.toki31_playwright)
"""

import logging
import re
from typing import Optional, Union, List, Dict

from lib.proxy_session import (
    get_proxy_session_with_fallback,
    get_maskproxy_config,
    get_dataimpulse_config,
    create_proxy_session,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://toki31.com"

# 한국 주거용 프록시 세션 (MaskProxy Primary, DataImpulse Fallback)
_session, _active_proxy = get_proxy_session_with_fallback()


def fetch_home() -> Optional[str]:
    """toki31 홈 페이지 HTML."""
    return _get(f"{BASE_URL}/")


def fetch_ing() -> Optional[str]:
    """연재중 웹툰/소설 목록 (RSC payload 포함)."""
    return _get(f"{BASE_URL}/ing")


def fetch_novel_list() -> Optional[str]:
    """toki31 소설 목록 페이지 HTML (/novel)."""
    return _get(f"{BASE_URL}/novel")


def fetch_novel_detail(novel_id: Union[int, str]) -> Optional[str]:
    """개별 소설 상세 페이지 HTML."""
    return _get(f"{BASE_URL}/novel/{novel_id}")


def fetch_chapter(novel_id: Union[int, str], chapter_id: Union[int, str]) -> Optional[str]:
    """소설 본문(회차) HTML."""
    return _get(f"{BASE_URL}/novel/{novel_id}/{chapter_id}")




def _get(url: str, timeout: int = 15) -> Optional[str]:
    """curl_cffi GET 요청. 실패 시 fallback 프록시로 재시도."""
    if _session is None:
        logger.error("프록시 미설정 - _get() 호출 불가")
        return None

    try:
        resp = _session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.text

        # 403 또는 프록시 관련 에러 시 fallback 시도
        if resp.status_code in (403, 407, 502, 503):
            return _get_with_fallback(url, timeout)

        return None
    except Exception:
        return _get_with_fallback(url, timeout)


def _get_with_fallback(url: str, timeout: int) -> Optional[str]:
    """Fallback 프록시로 재시도."""
    if _active_proxy is None:
        return None

    maskproxy = get_maskproxy_config()
    dataimpulse = get_dataimpulse_config()

    # 현재 프록시와 다른 것으로 시도
    fallback = dataimpulse if _active_proxy.name == "maskproxy" else maskproxy

    if fallback.is_configured:
        try:
            session = create_proxy_session("chrome131", fallback)
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                logger.info(f"Fallback 성공: {fallback.name}")
                return resp.text
        except Exception:
            pass

    return None


# --- Next.js RSC payload 파서 ---

def parse_rsc_payload(html: str) -> List[Dict]:
    """RSC payload에서 에피소드 데이터 추출.

    Next.js App Router의 RSC payload는 <script> 태그에:
    self.__next_f.push([1, "..."])

    Returns:
        [{episodeCount, latestEpisodeNumber, rating, ...}, ...]
    """
    # RSC payload 추출
    rsc_scripts = re.findall(
        r'<script[^>]*>self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>',
        html,
        re.DOTALL,
    )

    episodes = []
    for script in rsc_scripts:
        # JSON 문자열 디코딩
        try:
            decoded = script.encode().decode('unicode_escape')
        except Exception:
            decoded = script

        # 에피소드 데이터 패턴 매칭
        # Next.js RSC는 복잡한 구조지만, 핵심 데이터는 JSON-like 형태
        ep_matches = re.findall(
            r'"episodeCount"\s*:\s*(\d+)|'
            r'"latestEpisodeNumber"\s*:\s*(\d+)|'
            r'"rating"\s*:\s*([\d.]+)|'
            r'"title"\s*:\s*"([^"]*)"',
            decoded,
        )

        if ep_matches:
            ep_data = {}
            for match in ep_matches:
                if match[0]:
                    ep_data['episodeCount'] = int(match[0])
                elif match[1]:
                    ep_data['latestEpisodeNumber'] = int(match[1])
                elif match[2]:
                    ep_data['rating'] = float(match[2])
                elif match[3]:
                    ep_data['title'] = match[3]
            if ep_data:
                episodes.append(ep_data)

    return episodes


def extract_episode_data(html: str) -> List[Dict]:
    """/ing 페이지에서 episode 데이터 추출.

    HTML 구조에서 회차 정보를 파싱:
    - 회차 번호, 평점, 좋아요 수 등

    Returns:
        [{episodeCount, latestEpisodeNumber, rating, title, ...}, ...]
    """
    episodes = []

    # RSC payload가 있으면 우선 사용
    rsc_episodes = parse_rsc_payload(html)
    if rsc_episodes:
        return rsc_episodes

    # RSC payload가 없으면 HTML 파싱
    # 일반적인 Next.js 카드 구조
    card_pattern = re.compile(
        r'<a[^>]*href="[^"]*/novel/(\d+)[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for m in card_pattern.finditer(html):
        novel_id = m.group(1)
        inner = m.group(2)

        # 제목 추출
        title_m = re.search(r'<[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)', inner)
        title = title_m.group(1).strip() if title_m else ""

        # 회차 정보 추출
        ep_m = re.search(r'(\d+)\s*화', inner)
        episode_count = int(ep_m.group(1)) if ep_m else 0

        # 평점 추출
        rating_m = re.search(r'(\d+\.?\d*)\s*점|rating["\s:]+(\d+\.?\d*)', inner)
        rating = float(rating_m.group(1) or rating_m.group(2)) if rating_m else 0.0

        if title or episode_count:
            episodes.append({
                'novelId': novel_id,
                'title': title,
                'episodeCount': episode_count,
                'rating': rating,
            })

    return episodes


def parse_chapter_body(html: str) -> str:
    """회차 본문 HTML에서 본문 텍스트 추출.

    toki31은 Next.js 기반이나 본문 구조는 유사.
    """
    # Next.js RSC payload에서 본문 추출 시도
    rsc_scripts = re.findall(
        r'<script[^>]*>self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>',
        html,
        re.DOTALL,
    )

    for script in rsc_scripts:
        try:
            decoded = script.encode().decode('unicode_escape')
        except Exception:
            decoded = script

        # 본문 패턴 (일반적인 웹소설 본문)
        body_m = re.search(
            r'"content"\s*:\s*"((?:[^"\\]|\\.){100,})"',
            decoded,
        )
        if body_m:
            body = body_m.group(1)
            # 유니코드 이스케이프 해제
            try:
                body = body.encode().decode('unicode_escape')
            except Exception:
                pass
            # HTML 태그 제거
            body = re.sub(r'<[^>]+>', '\n', body)
            body = re.sub(r'\n\s*\n', '\n', body)
            return body.strip()

    # RSC payload에 없으면 일반 HTML 파싱
    body_m = re.search(
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL,
    )
    if body_m:
        body = body_m.group(1)
        body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
        body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.DOTALL)
        body = re.sub(r'<[^>]+>', '\n', body)
        body = re.sub(r'\n\s*\n', '\n', body)
        return body.strip()

    return ""
