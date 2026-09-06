#!/usr/bin/env python3
# Status: new
# Path: lib/collector_lite.py — lib/collector.py의 경량 fallback
"""경량 챕터 수집기 — Playwright 1회만 사용, 이후 curl_cffi API 직접 호출.

전략:
  Phase 1: Playwright 1회 실행 → ad/ack + nv-issue로 세션/쿠키 확보
  Phase 2: curl_cffi로 novel-content API 직접 호출 + 복호화 (재사용)
  Phase 3: 실패 시 lib/collector.py의 ChapterCollector로 fallback

이렇게 하면 Playwright 페이지 로드 시간(~5s)을 회차당 1회로 줄일 수 있다.
101회차 기준: ~15분 → ~5분 (Playwright 1회 + curl_cffi 100회 × 2~3s)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from curl_cffi import requests as creq

from lib.toki31_playwright import derive_key, decrypt_payload, extract_text_from_content

logger = logging.getLogger(__name__)

_ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"


@dataclass
class ProxyCredentials:
    username: str
    password: str
    host: str
    port: str


def _load_proxy_env() -> dict:
    env = {}
    if _ENV_LOCAL.exists():
        for line in _ENV_LOCAL.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _get_proxy_creds(priority: str = "dataimpulse") -> Optional[ProxyCredentials]:
    """환경변수에서 프록시 자격증명 로드."""
    env = _load_proxy_env()
    providers = {
        "dataimpulse": ("DATAIMPULSE", "gw.dataimpulse.com", "823"),
        "maskproxy": ("MASKPROXY", "gw.maskproxy.io", "1288"),
    }
    order = [priority, "dataimpulse" if priority != "dataimpulse" else "maskproxy"]
    for key in order:
        prefix, default_host, default_port = providers.get(key, ("DATAIMPULSE", "gw.dataimpulse.com", "823"))
        user = env.get(f"{prefix}_USER", "")
        password = env.get(f"{prefix}_PASS", "")
        host = env.get(f"{prefix}_HOST", default_host)
        port = env.get(f"{prefix}_PORT", default_port)
        if user and password:
            if key == "dataimpulse" and "__cr." not in user:
                user = user + "__cr.kr"
            return ProxyCredentials(username=user, password=password, host=host, port=port)
    return None


def _create_curl_session(proxy: Optional[ProxyCredentials] = None) -> creq.Session:
    """curl_cffi 세션 생성 (프록시 적용)."""
    session = creq.Session(impersonate="chrome131")
    session.headers.update({"Accept-Language": "ko-KR,ko;q=0.9"})
    if proxy:
        proxy_url = f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
        session.proxies = {"https": proxy_url, "http": proxy_url}
    return session


def _b64url(data: bytes) -> str:
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


class LightweightCollector:
    """경량 챕터 수집기 — Playwright 1회 + curl_cffi N회.

    사용법:
        collector = LightweightCollector("dataimpulse")
        await collector.initialize("58455", "5784624")  # Playwright 1회
        body = await collector.collect("58455", "5784625")  # curl_cffi
        body = await collector.collect("58455", "5784626")  # curl_cffi
        # ...
        await collector.close()
    """

    def __init__(self, proxy_priority: str = "dataimpulse"):
        self._proxy = _get_proxy_creds(proxy_priority)
        self._session: Optional[creq.Session] = None
        self._nv_session: Optional[str] = None
        self._playwright_collector = None
        self._initialized = False

    async def initialize(self, novel_id: str, chapter_id: str) -> bool:
        """Playwright 1회 실행으로 ad/ack + nv 세션 확보.

        Returns:
            성공 시 True
        """
        if self._proxy is None:
            logger.error("프록시 자격증명 없음")
            return False

        from lib.collector import ChapterCollector, CollectorConfig

        config = CollectorConfig(
            name="toki31",
            base_url="https://toki31.com",
            rate_limit_interval=0,
            use_proxy=True,
            proxy_priority="dataimpulse" if "dataimpulse" in self._proxy.host else "maskproxy",
        )

        collector = ChapterCollector(config)
        await collector.start()

        # 1회 수집으로 Playwright 세션 확보
        body = await collector.collect_chapter(novel_id, chapter_id)

        if body:
            # Playwright 세션에서 nv 쿠키 추출
            cookies = await collector._context.cookies()
            nv_cookie = ""
            for c in cookies:
                if c["name"] == "nv":
                    nv_cookie = c["value"]
                    break

            if nv_cookie:
                # curl_cffi 세션 생성 + 쿠키 설정
                self._session = _create_curl_session(self._proxy)
                # nv 쿠키를 curl_cffi 세션에 설정
                self._session.cookies.set("nv", nv_cookie, domain="toki31.com")
                self._nv_session = nv_cookie
                self._initialized = True
                logger.info("경량 수집기 초기화 완료 (nv 쿠키 확보)")
                await collector.close()
                return True

        logger.warning("Playwright 초기화 실패, fallback 유지")
        self._playwright_collector = collector
        self._initialized = True
        return True  # fallback 사용

    async def collect(self, novel_id: str, chapter_id: str) -> Optional[str]:
        """단일 챕터 수집 — curl_cffi 직접 API 호출.

        Fallback: curl_cffi 실패 시 Playwright 사용.

        Args:
            novel_id: 소설 ID
            chapter_id: 회차 ID

        Returns:
            본문 텍스트 또는 None
        """
        if not self._initialized:
            logger.error("초기화되지 않음: initialize() 먼저 호출")
            return None

        # Phase 1: curl_cffi로 직접 API 호출
        if self._session and self._nv_session:
            body = await self._collect_via_curl(novel_id, chapter_id)
            if body:
                return body
            logger.info(f"curl_cffi 실패, Playwright fallback: {novel_id}/{chapter_id}")

        # Phase 2: Playwright fallback
        return await self._collect_via_playwright(novel_id, chapter_id)

    async def _collect_via_curl(self, novel_id: str, chapter_id: str) -> Optional[str]:
        """curl_cffi로 novel-content API 직접 호출 + 복호화.

        Playwright 없이 RSC payload에서 token 추출 → API 호출 → 복호화.
        """
        session = self._session

        # 1. 챕터 페이지 HTML (RSC payload 확보)
        r = session.get(
            f"https://toki31.com/novel/{novel_id}/{chapter_id}",
            timeout=15,
        )
        if r.status_code != 200:
            logger.debug(f"  curl: 챕터 페이지 HTTP {r.status_code}")
            return None

        import re
        rsc_text = r.text

        # 2. RSC payload에서 token 추출 (JWT: ey...)
        # RSC payload는 escape된 따옴표 사용: \"token\":\"eyJ...\"
        token = None
        for pattern in [
            r'\\?"token\\?":\\?"(ey[A-Za-z0-9_.-]+)\\?"',
            r'"token"\s*:\s*"(ey[A-Za-z0-9_.-]+)"',
            r'"token","(ey[A-Za-z0-9_.-]+)"',
        ]:
            m = re.search(pattern, rsc_text)
            if m:
                token = m.group(1)
                break

        if not token:
            logger.debug("  curl: RSC token 추출 실패")
            return None

        # 3. nonce 생성
        nonce = _b64url(os.urandom(24))

        # 4. proof = HMAC-SHA256(nv_session, nonce)
        proof = hmac.new(
            self._nv_session.encode(),
            nonce.encode(),
            hashlib.sha256,
        ).digest()
        proof_b64 = _b64url(proof)

        # 5. novel-content API 호출
        r = session.post(
            "https://toki31.com/api/novel-content",
            json={
                "novelId": novel_id,
                "episodeId": chapter_id,
                "token": token,
                "nonce": nonce,
                "proof": proof_b64,
            },
            headers={
                "x-novel-client": "shadow-v3",
                "x-nv-session": self._nv_session,
                "referer": f"https://toki31.com/novel/{novel_id}/{chapter_id}",
            },
            timeout=15,
        )

        if r.status_code != 200:
            logger.debug(f"  curl: novel-content API HTTP {r.status_code}")
            return None

        data = r.json()
        if not data.get("ok") or not data.get("payload"):
            logger.debug(f"  curl: novel-content API 실패: {data.get('error')}")
            return None

        payload = data["payload"]

        # 6. 복호화
        try:
            key = derive_key(self._nv_session, novel_id, chapter_id)
            decrypted = decrypt_payload(payload, key)
            return extract_text_from_content(decrypted)
        except Exception as e:
            logger.debug(f"  curl: 복호화 실패: {e}")
            return None

    async def _collect_via_playwright(self, novel_id: str, chapter_id: str) -> Optional[str]:
        """Playwright fallback."""
        if self._playwright_collector is None:
            # 늦은 초기화
            from lib.collector import ChapterCollector, CollectorConfig
            config = CollectorConfig(
                name="toki31",
                base_url="https://toki31.com",
                rate_limit_interval=0,
                use_proxy=True,
                proxy_priority="dataimpulse" if self._proxy and "dataimpulse" in self._proxy.host else "maskproxy",
            )
            self._playwright_collector = ChapterCollector(config)
            await self._playwright_collector.start()

        return await self._playwright_collector.collect_chapter(novel_id, chapter_id)

    async def close(self):
        """리소스 정리."""
        if self._playwright_collector:
            await self._playwright_collector.close()
            self._playwright_collector = None
        self._session = None
        self._initialized = False