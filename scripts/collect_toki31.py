#!/usr/bin/env python3
# Status: new
# Path: 직접 실행
"""toki31 소설 전회차 수집기 — lib.collector + lib.storage 사용.

사용법:
  python3 scripts/collect_toki31.py 58455 "회귀자 사용설명서"

설정:
  .env.local에 DataImpulse 또는 MaskProxy 자격증명 필요
  (lib/collector.py가 자동 로드)

수집 결과:
  /opt/ai_data/flaresolverr/novels/{novel_id}/
  ├── {chapter_id}.json
  ├── meta.json
  └── ...
"""

import asyncio
import logging
import sys
import re
from pathlib import Path

# 경로 설정
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps/backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collect_toki31")


async def fetch_chapter_list(novel_id: str) -> tuple[str, list[tuple[str, str]]]:
    """DataImpulse 프록시로 소설 상세 페이지에서 회차 목록 추출.

    Returns:
        (novel_title, [(chapter_id, chapter_title), ...])
    """
    from lib.collector import _load_proxy_env
    from curl_cffi import requests as creq

    env = _load_proxy_env()
    proxy_user = env.get("DATAIMPULSE_USER", "") + "__cr.kr"
    proxy_pass = env.get("DATAIMPULSE_PASS", "")
    proxy_host = env.get("DATAIMPULSE_HOST", "gw.dataimpulse.com")
    proxy_port = env.get("DATAIMPULSE_PORT", "823")
    proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"

    session = creq.Session(impersonate="chrome131")
    session.headers.update({"Accept-Language": "ko-KR,ko;q=0.9"})
    session.proxies = {"https": proxy_url, "http": proxy_url}

    logger.info(f"소설 페이지 로드 중: https://toki31.com/novel/{novel_id}")
    r = session.get(f"https://toki31.com/novel/{novel_id}", timeout=15)
    r.raise_for_status()

    # 제목 추출
    title_m = re.search(r"<title>(.*?)\s*-\s*뉴토끼</title>", r.text)
    novel_title = title_m.group(1).strip() if title_m else f"novel_{novel_id}"
    logger.info(f"제목: {novel_title}")

    # 회차 ID 추출 (중복 제거, 정렬)
    chapter_ids = sorted(set(re.findall(rf"/novel/{novel_id}/(\d+)", r.text)))
    logger.info(f"총 {len(chapter_ids)}개 회차 발견")

    # (chapter_id, chapter_title) 형태로 변환 (제목은 상세 페이지에서 추출 필요)
    chapters = [(cid, "") for cid in chapter_ids]
    return novel_title, chapters


async def main():
    if len(sys.argv) < 2:
        print("사용법: python3 scripts/collect_toki31.py <novel_id> [novel_title]")
        print("  예:  python3 scripts/collect_toki31.py 58455")
        sys.exit(1)

    novel_id = sys.argv[1]
    novel_title = sys.argv[2] if len(sys.argv) > 2 else ""

    # 회차 목록 조회
    fetched_title, chapters = await fetch_chapter_list(novel_id)
    if not novel_title:
        novel_title = fetched_title

    if not chapters:
        logger.error("회차 목록을 찾을 수 없습니다.")
        sys.exit(1)

    logger.info(f"수집 시작: '{novel_title}' ({novel_id}), 총 {len(chapters)}회차")

    # 수집기 설정
    from lib.collector import ChapterCollector, CollectorConfig
    from lib.storage import save_chapter

    config = CollectorConfig(
        name="toki31",
        base_url="https://toki31.com",
        rate_limit_interval=2,  # DataImpulse IP 회전 → 2초면 충분
        use_proxy=True,
        proxy_priority="dataimpulse",
        novel_title=novel_title,
        novel_id=novel_id,
    )

    collector = ChapterCollector(config)
    await collector.start()

    try:
        results = []
        total = len(chapters)

        for i, (chapter_id, _) in enumerate(chapters, 1):
            logger.info(f"[{i}/{total}] 회차 {chapter_id} 수집 중...")

            body = await collector.collect_chapter(novel_id, chapter_id)

            if body:
                # 저장
                success = save_chapter(
                    novel_title=novel_title,
                    wr_id=int(chapter_id),
                    body=body,
                    source="toki31",
                )
                if success:
                    logger.info(f"  ✅ [{i}/{total}] {chapter_id} 저장 완료 ({len(body)} chars)")
                else:
                    logger.error(f"  ❌ [{i}/{total}] {chapter_id} 저장 실패")
            else:
                logger.error(f"  ❌ [{i}/{total}] {chapter_id} 수집 실패")

            results.append({
                "chapter_id": chapter_id,
                "success": body is not None,
                "body_len": len(body) if body else 0,
            })

    finally:
        await collector.close()

    # 결과 요약
    success_count = sum(1 for r in results if r["success"])
    logger.info("=" * 40)
    logger.info(f"수집 완료: {success_count}/{total} 성공")
    logger.info(f"저장 위치: /opt/ai_data/flaresolverr/novels/{novel_title.replace(' ', '_')}/")


if __name__ == "__main__":
    asyncio.run(main())