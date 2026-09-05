#!/usr/bin/env python3
"""Brave Search로 문피아/조아라 책 URL 자동 검색.

DB의 4개 소설에 대해 "{제목} site:munpia.com" 또는 "{제목} site:joara.com"
검색하여 책 URL을 찾고, DB의 munpia_url/joara_url 컬럼에 저장.

Brave API 키는 secrets.env의 BRAVE_API_KEYS에 있음 (4개 키 로테이션).
"""

import json
import os
import re
import sys
import time
import logging
from pathlib import Path
from typing import Optional, List, Dict
from urllib.parse import quote

import requests

sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("brave_book_url")

# secrets.env의 BRAVE_API_KEYS 형식: "key1:KEY1,key2:KEY2,..."
BRAVE_KEYS = os.getenv("BRAVE_API_KEYS", "")
BRAVE_API_KEY_LIST = []
if BRAVE_KEYS:
    for pair in BRAVE_KEYS.split(","):
        if ":" in pair:
            BRAVE_API_KEY_LIST.append(pair.split(":", 1)[1].strip())

if not BRAVE_API_KEY_LIST:
    log.error("BRAVE_API_KEYS 환경변수 없음")
    sys.exit(1)

NEON = "postgresql://neondb_owner:npg_dtpE5bK2eAFv@ep-round-hill-azleavuh.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")

key_index = 0


def brave_search(query: str, count: int = 5) -> List[Dict]:
    """Brave Search API 호출 (키 로테이션)."""
    global key_index
    last_error = None
    for attempt in range(len(BRAVE_API_KEY_LIST)):
        api_key = BRAVE_API_KEY_LIST[key_index % len(BRAVE_API_KEY_LIST)]
        key_index += 1
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                params={
                    "q": query,
                    "count": count,
                    "country": "KR",
                    "search_lang": "ko",
                },
                timeout=15,
            )
            if resp.status_code == 429:
                log.warning(f"  키 {key_index} rate limit, 다음 키 시도")
                last_error = "rate_limited"
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("web", {}).get("results", [])
        except Exception as e:
            last_error = e
            continue
    log.error(f"  모든 키 실패: {last_error}")
    return []


def extract_book_url(results: List[Dict], domain: str) -> Optional[str]:
    """검색 결과에서 특정 도메인의 책 URL 추출.

    Args:
        results: Brave Search 결과
        domain: 'munpia.com' 또는 'joara.com'

    Returns:
        책 상세 URL 또는 None
    """
    candidates = []
    for r in results:
        url = r.get("url", "")
        if domain in url:
            # 책 상세 페이지 패턴 (book_view, novel_view, book detail, view)
            if any(p in url for p in ["book_view", "novel_view", "book_view", "detail", "view", "/book/", "/novel/"]):
                candidates.append(url)
    if not candidates:
        return None
    # 첫 번째 매치
    return candidates[0]


def find_book_urls(title: str) -> Dict[str, Optional[str]]:
    """제목으로 문피아/조아라 책 URL 찾기."""
    log.info(f"\n=== {title} ===")

    results = {"munpia_url": None, "joara_url": None}

    # 문피아
    log.info("  문피아 검색...")
    munpia_results = brave_search(f"{title} site:munpia.com", 5)
    if munpia_results:
        for r in munpia_results[:2]:
            log.info(f"    - {r.get('url', '?')[:80]}")
    results["munpia_url"] = extract_book_url(munpia_results, "munpia.com")
    log.info(f"  → {results['munpia_url'] or '(없음)'}")

    # 짧은 대기
    time.sleep(1)

    # 조아라
    log.info("  조아라 검색...")
    joara_results = brave_search(f"{title} site:joara.com", 5)
    if joara_results:
        for r in joara_results[:2]:
            log.info(f"    - {r.get('url', '?')[:80]}")
    results["joara_url"] = extract_book_url(joara_results, "joara.com")
    log.info(f"  → {results['joara_url'] or '(없음)'}")

    return results


def update_db_and_local(novel_id: str, urls: Dict[str, Optional[str]]) -> None:
    """DB + meta.json에 munpia_url/joara_url 업데이트."""
    import psycopg2

    # DB에 컬럼 추가 (없으면)
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    # 컬럼 존재 확인
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ebook_novels'
        AND column_name IN ('munpia_url', 'joara_url')
    """)
    existing_cols = [row[0] for row in cur.fetchall()]

    for col in ['munpia_url', 'joara_url']:
        if col not in existing_cols:
            log.info(f"  DB 컬럼 추가: {col}")
            cur.execute(f"ALTER TABLE ebook_novels ADD COLUMN {col} TEXT")

    conn.commit()
    cur.close()
    conn.close()

    # DB 업데이트
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("""
        UPDATE ebook_novels
        SET munpia_url = %s, joara_url = %s, updated_at = NOW()
        WHERE id = %s
    """, (urls.get("munpia_url"), urls.get("joara_url"), novel_id))
    conn.commit()
    cur.close()
    conn.close()

    # 로컬 meta.json
    meta_file = DATA_DIR / novel_id / "meta.json"
    if meta_file.exists():
        with open(meta_file) as f:
            local = json.load(f)
        local["munpia_url"] = urls.get("munpia_url")
        local["joara_url"] = urls.get("joara_url")
        with open(meta_file, "w") as f:
            json.dump(local, f, ensure_ascii=False, indent=2)


def main():
    """DB의 모든 소설에 대해 문피아/조아라 URL 검색."""
    import psycopg2

    # DB에서 소설 목록
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM ebook_novels WHERE id IN ('하남자의_탑_공략법', '오늘만_사는_기사', '게임_속_바바리안으로_살아남기', '화산귀환') ORDER BY id")
    novels = cur.fetchall()
    cur.close()
    conn.close()

    print(f"=== {len(novels)}개 소설에 대해 문피아/조아라 URL 검색 ===\n")

    for novel_id, title in novels:
        urls = find_book_urls(title)
        update_db_and_local(novel_id, urls)
        log.info(f"  → DB 저장 완료")

    print("\n=== 최종 DB 상태 ===")
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, munpia_url, joara_url FROM ebook_novels WHERE id IN ('하남자의_탑_공략법', '오늘만_사는_기사', '게임_속_바바리안으로_살아남기', '화산귀환') ORDER BY id")
    for row in cur.fetchall():
        log.info(f"  {row[0]}: munpia={row[1] or '-'} | joara={row[2] or '-'}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()