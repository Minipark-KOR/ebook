#!/usr/bin/env python3
# Status: new
# Path: scripts/collect_toki31.py, 향후 사이트별 수집기
"""챕터 수집기 베이스 — Playwright + 프록시 + rate limit + storage 통합.

사이트별 수집기는 이 모듈을 상속/사용하여 구현:
  from lib.collector import ChapterCollector, CollectorConfig

  config = CollectorConfig(name="toki31", base_url="https://toki31.com", ...)
  collector = ChapterCollector(config)
  await collector.start()
  result = await collector.collect_chapter("58011", "5384505")
  await collector.close()
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# .env.local 경로 (lib/ 상위 디렉토리 = backend/)
_ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"


def _load_proxy_env() -> dict:
    """Load proxy credentials from .env.local."""
    env = {}
    if _ENV_LOCAL.exists():
        for line in _ENV_LOCAL.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


@dataclass
class CollectorConfig:
    """수집기 설정 — 사이트별로 다르게 구성.

    Attributes:
        name: 사이트 식별자 (예: "toki31", "bookto31")
        base_url: 사이트 베이스 URL
        rate_limit_interval: 요청 간 최소 간격(초). 0이면 제한 없음.
            toki31(DataImpulse IP 회전)은 1~2초.
            bookto31(FlareSolverr 고정 IP)은 480초(8분) 필요.
        use_proxy: 프록시 사용 여부
        proxy_priority: "dataimpulse" | "maskproxy"
        chapter_url_pattern: 챕터 URL 패턴 (예: "/novel/{novel_id}/{chapter_id}")
        fetch_chapter_list: 소설 페이지에서 회차 목록 추출 함수
    """
    name: str
    base_url: str
    rate_limit_interval: int = 0
    use_proxy: bool = True
    proxy_priority: str = "dataimpulse"
    novel_title: str = ""
    novel_id: str = ""


@dataclass
class ProxyConfig:
    """프록시 연결 설정."""
    server: str
    username: str
    password: str


class ChapterCollector:
    """챕터 수집기 — Playwright 세션 관리 + 프록시 + rate limit.

    사용법:
        collector = ChapterCollector(config)
        await collector.start()
        try:
            result = await collector.collect_chapter(novel_id, chapter_id)
            # ... 저장 로직
        finally:
            await collector.close()
    """

    def __init__(self, config: CollectorConfig):
        self.config = config
        self._browser = None
        self._context = None
        self._page = None
        self._last_request_time: float = 0.0
        self._proxy_config: Optional[ProxyConfig] = None

    def _resolve_proxy(self) -> Optional[ProxyConfig]:
        """설정에 따라 프록시 설정 반환."""
        if not self.config.use_proxy:
            return None

        env = _load_proxy_env()
        priorities = {
            "dataimpulse": ("DATAIMPULSE", "gw.dataimpulse.com", "823"),
            "maskproxy": ("MASKPROXY", "gw.maskproxy.io", "1288"),
        }

        # 우선순위 순서
        order = ["dataimpulse", "maskproxy"]
        if self.config.proxy_priority == "maskproxy":
            order = ["maskproxy", "dataimpulse"]

        for key in order:
            prefix, default_host, default_port = priorities[key]
            user = env.get(f"{prefix}_USER", "")
            password = env.get(f"{prefix}_PASS", "")
            host = env.get(f"{prefix}_HOST", default_host)
            port = env.get(f"{prefix}_PORT", default_port)

            if user and password:
                # DataImpulse 한국 IP targeting
                if key == "dataimpulse" and "__cr." not in user:
                    user = user + "__cr.kr"
                return ProxyConfig(
                    server=f"http://{host}:{port}",
                    username=user,
                    password=password,
                )
        return None

    def _apply_rate_limit(self):
        """요청 간 rate limit 적용."""
        interval = self.config.rate_limit_interval
        if interval <= 0:
            return
        elapsed = time.time() - self._last_request_time
        if elapsed < interval:
            wait = interval - elapsed
            logger.debug(f"Rate limit: waiting {wait:.1f}s")
            time.sleep(wait)

    def _mark_request(self):
        """요청 시간 기록."""
        self._last_request_time = time.time()

    async def start(self):
        """Playwright 브라우저 시작 + 프록시 설정."""
        from playwright.async_api import async_playwright

        proxy = self._resolve_proxy()
        self._proxy_config = proxy

        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = {
                "server": proxy.server,
                "username": proxy.username,
                "password": proxy.password,
            }
            logger.info(f"프록시 사용: {proxy.server} (@{proxy.username[:8]}...)")
        else:
            logger.info("프록시 미사용 (직접 접속)")

        p = await async_playwright().start()
        self._browser = await p.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        self._page = await self._context.new_page()
        logger.info(f"브라우저 시작 완료 ({self.config.name})")

    async def close(self):
        """브라우저 종료."""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page = None
            logger.info("브라우저 종료")

    async def navigate(self, url: str, max_attempts: int = 3) -> bool:
        """페이지 로드 (재시도 포함)."""
        page = self._page
        for attempt in range(max_attempts):
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if resp and resp.status == 200:
                    return True
            except Exception as e:
                logger.warning(f"  페이지 로드 실패 ({attempt+1}/{max_attempts}): {e}")
                if attempt < max_attempts - 1:
                    await page.wait_for_timeout(3000)
        return False

    async def collect_chapter(
        self,
        novel_id: str,
        chapter_id: str,
        chapter_title: str = "",
    ) -> Optional[str]:
        """단일 챕터 본문 수집 (rate limit 적용).

        Args:
            novel_id: 소설 ID
            chapter_id: 회차 ID
            chapter_title: 회차 제목 (선택, 저장 시 사용)

        Returns:
            본문 텍스트 또는 None
        """
        self._apply_rate_limit()

        page = self._page
        target_url = f"{self.config.base_url}/novel/{novel_id}/{chapter_id}"
        content_payload = {}

        async def on_response(response):
            if "/api/novel-content" in response.url:
                try:
                    data = await response.json()
                    if data.get("ok") and data.get("payload"):
                        content_payload["data"] = data
                except Exception:
                    pass

        # novel-content API 응답 대기 (asyncio.Event 기반, 최대 15s)
        # 응답 JSON 파싱까지 완료된 시점을 감지하여 지연 최소화
        response_event = asyncio.Event()

        async def _waiting_on_response(response):
            if "/api/novel-content" in response.url and not content_payload.get("data"):
                try:
                    data = await response.json()
                    if data.get("ok") and data.get("payload"):
                        content_payload["data"] = data
                        response_event.set()
                except Exception:
                    pass

        page.on("response", _waiting_on_response)

        # 페이지 로드
        loaded = await self.navigate(target_url)
        if not loaded:
            logger.error(f"  페이지 로드 실패: {target_url}")
            return None

        # novel-content API 응답 대기 (이미 수신되었으면 즉시 진행)
        if not content_payload.get("data"):
            try:
                await asyncio.wait_for(response_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass

        if not content_payload.get("data"):
            logger.error(f"  novel-content API 응답 없음: {target_url}")
            return None

        # nv 쿠키 추출
        cookies = await self._context.cookies()
        nv_cookie = ""
        for c in cookies:
            if c["name"] == "nv":
                nv_cookie = c["value"]
                break

        if not nv_cookie:
            logger.error("  nv cookie not found")
            return None

        payload = content_payload["data"].get("payload", "")
        if not payload:
            logger.error("  Empty payload from novel-content")
            return None

        # 복호화
        from lib.toki31_playwright import derive_key, decrypt_payload, extract_text_from_content
        try:
            key = derive_key(nv_cookie, novel_id, chapter_id)
            decrypted = decrypt_payload(payload, key)
            content_text = extract_text_from_content(decrypted)
        except Exception as e:
            logger.error(f"  복호화 실패: {e}")
            return None

        if not content_text or len(content_text) < 50:
            logger.error(f"  본문 너무 짧음: {len(content_text) if content_text else 0} chars")
            return None

        self._mark_request()
        return content_text

    async def collect_chapters(
        self,
        novel_id: str,
        chapters: list[tuple[str, str]],
        save_func: Optional[Callable[[str, str, str], Awaitable[bool]]] = None,
        progress_callback: Optional[Callable[[int, int, str, bool], None]] = None,
    ) -> list[dict]:
        """여러 챕터 순차 수집.

        Args:
            novel_id: 소설 ID
            chapters: [(chapter_id, chapter_title), ...] 리스트
            save_func: 저장 함수 async (novel_title, chapter_id, body) → bool
            progress_callback: 진행 콜백 (index, total, chapter_id, success)

        Returns:
            [{chapter_id, title, success, body_len, error}, ...]
        """
        results = []
        total = len(chapters)

        for i, (chapter_id, chapter_title) in enumerate(chapters, 1):
            logger.info(f"[{i}/{total}] 회차 {chapter_id} 수집 중...")
            body = await self.collect_chapter(novel_id, chapter_id, chapter_title)

            result = {
                "chapter_id": chapter_id,
                "title": chapter_title or "",
                "success": body is not None,
                "body_len": len(body) if body else 0,
                "error": None,
            }

            if body:
                if save_func:
                    try:
                        await save_func(chapter_id, body, chapter_title)
                    except Exception as e:
                        result["error"] = f"저장 실패: {e}"
                        result["success"] = False
            else:
                result["error"] = "수집 실패"

            results.append(result)

            if progress_callback:
                progress_callback(i, total, chapter_id, result["success"])

            logger.info(
                f"  {'✅' if result['success'] else '❌'} "
                f"[{i}/{total}] {chapter_title or chapter_id} "
                f"({result['body_len']} chars)"
            )

        return results