#!/usr/bin/env python3
# Status: new
# Path: scripts/collect_bookto31.py, 향후 사이트별 수집기
"""bookto31 챕터 수집기 — FlareSolverr + rate limit + 본문 추출.

lib/collector.py의 ChapterCollector와 동일 패턴이지만,
bookto31 특성에 맞춰 구성:

1. FlareSolverr 세션 재사용 (브라우저 1회, cf_clearance/IP 재사용)
2. rate_limit=True (8분 간격) — FlareSolverr가 고정 IP라 차단 방지 필수
3. 데이터 최적화 — HTML 전체(245KB) 대신 본문(5.8KB)만 추출

사용법:
  collector = Bookto31Collector()
  await collector.start()
  body = await collector.collect_chapter(21431)
  await collector.close()

주의:
- bookto31은 FlareSolverr(고정 IP) 기반이라 toki31과 달리 IP 회전 없음
- rate_limit_module에서 8분 간격 제한 (동일 URL 중복 요청 방지)
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# .env.local 경로 (lib/ 상위 디렉토리 = backend/)
_ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"


class Bookto31Collector:
    """bookto31 전용 수집기 — FlareSolverr 세션 + rate limit + 본문 추출.

    사용법:
        collector = Bookto31Collector()
        await collector.start()
        try:
            body = await collector.collect_chapter(novel_wr_id)
            save_func(...)
        finally:
            await collector.close()
    """

    BASE_URL = "https://bookto31.com"

    def __init__(self, rate_limit: bool = True):
        self._rate_limit = rate_limit
        self._fs = None

    async def start(self):
        """FlareSolverr 세션 시작."""
        from lib.flaresolverr_client import FlareSolverrSession

        # rate_limit=True: 8분 + ±2분 jitter (북토끼 전체 URL 공용 DB)
        self._fs = FlareSolverrSession(
            rate_limit=self._rate_limit,
            interval=480,
            jitter=120,
        )
        logger.info(f"bookto31 FlareSolverr 세션 시작 (rate_limit={self._rate_limit})")

    async def close(self):
        """세션 종료."""
        self._fs = None

    async def collect_chapter(self, wr_id: int) -> Optional[str]:
        """특정 회차 본문 수집.

        Args:
            wr_id: 대하여 회차 wr_id

        Returns:
            본문 텍스트 (GNUBOARD5 HTML 파싱 후) 또는 None
        """
        if self._fs is None:
            await self.start()

        target_url = f"{self.BASE_URL}/bbs/board.php?bo_table=novel&wr_id={wr_id}"

        # FlareSolverr로 HTML 가져오기
        html = await asyncio.to_thread(
            self._fs.fetch,
            target_url,
            3,  # max_attempts
        )
        if not html:
            logger.error(f"  본문 HTML fetch 실패: {target_url}")
            return None

        # GNUBOARD5 파싱 (본문만 추출)
        from services.bookto31 import parse_chapter_body
        body = parse_chapter_body(html)
        if not body or len(body) < 50:
            logger.error(f"  본문 파싱 실패: {wr_id} ({len(body) if body else 0} chars)")
            return None

        logger.debug(f"  본문 추출 완료: {wr_id} ({len(body)} chars)")
        return body

    async def collect_novel(self, novel_main_wr_id: int, max_pages: int = 50) -> list[dict]:
        """소설의 모든 회차 수집.

        Args:
            novel_main_wr_id: 소설 메인 페이지 wr_id
            max_pages: 회차 목록 최대 페이지 수

        Returns:
            [{wr_id, title, success, body_len}, ...]
        """
        if self._fs is None:
            await self.start()

        # 회차 목록 추출 (작품 메인 페이지에서)
        from services.bookto31 import extract_chapter_wr_ids_from_index

        novel_url = f"{self.BASE_URL}/bbs/board.php?bo_table=novel&wr_id={novel_main_wr_id}"
        html = await asyncio.to_thread(self._fs.fetch, novel_url, 3)
        if not html:
            logger.error(f"  소설 메인 페이지 fetch 실패: {novel_url}")
            return []

        chapters = extract_chapter_wr_ids_from_index(html)
        logger.info(f"  발견된 회차: {len(chapters)}개")

        results = []
        for i, (wr_id, chapter_num) in enumerate(chapters, 1):
            logger.info(f"[{i}/{len(chapters)}] 회차 {wr_id} 수집 중...")
            body = await self.collect_chapter(wr_id)
            result = {
                "wr_id": wr_id,
                "chapter": chapter_num,
                "success": body is not None,
                "body_len": len(body) if body else 0,
            }
            results.append(result)
            logger.info(
                f"  {'✅' if result['success'] else '❌'} "
                f"[{i}/{len(chapters)}] {chapter_num}화 ({result['body_len']} chars)"
            )

        return results