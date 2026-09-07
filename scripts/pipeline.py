#!/usr/bin/env python3
"""ebooklib 파이프라인 — 체인 방식 단계별 실행.

각 단계는 독립적으로 실행되며, 이전 단계의 결과물(파일)을 입력으로 받음.
한 단계가 실패해도 다음 단계에 영향 없음.

사용법:
  python3 scripts/pipeline.py discover <wr_id> [novel_title] [max_pages]
  python3 scripts/pipeline.py collect [--limit N]
  python3 scripts/pipeline.py enrich [novel_id]
  python3 scripts/pipeline.py index [novel_id]
  python3 scripts/pipeline.py revalidate [novel_id]
  python3 scripts/pipeline.py all <wr_id> [novel_title]  # 전체 체인 실행
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger('pipeline')

WATCHER_DIR = Path('/opt/ai_data/flaresolverr/ebook_watcher')
WATCHER_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE = WATCHER_DIR / 'queue.json'
STATUS_FILE = WATCHER_DIR / 'status.json'
CHAPTER_DELAY_SEC = 300

# === 1단계: DISCOVER — wr_id 발견 → 큐에 추가 ===

def run_discover(wr_id: int, novel_title: str = "", max_pages: int = 50) -> int:
    """북토끼 작품 메인에서 모든 회차 wr_id 발견 → 큐에 추가."""
    from services.bookto31 import extract_chapter_wr_ids_from_index
    from lib.flaresolverr_client import FlareSolverrSession

    fs = FlareSolverrSession(rate_limit=False)
    all_chapters = []
    seen = set()

    for spage in range(1, max_pages + 1):
        url = f"https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={wr_id}&spage={spage}"
        html = fs.fetch(url)
        if not html or len(html) < 1000:
            log.info(f"  spage={spage}: 응답 없음, 중단")
            break

        page_chapters = extract_chapter_wr_ids_from_index(html)
        if not page_chapters:
            log.info(f"  spage={spage}: 회차 없음, 중단")
            break

        new = 0
        for ch_wr_id, chapter in page_chapters:
            if ch_wr_id not in seen and ch_wr_id != wr_id:
                seen.add(ch_wr_id)
                all_chapters.append((ch_wr_id, chapter))
                new += 1
        log.info(f"  spage={spage}: {new}개 신규 (누적 {len(all_chapters)})")
        if new == 0 and spage > 1:
            break

    # 큐에 추가
    queue = _load_queue()
    existing_ids = {item['wr_id'] for item in queue}
    added = 0
    for ch_wr_id, chapter in all_chapters:
        if ch_wr_id not in existing_ids:
            queue.append({
                "wr_id": ch_wr_id,
                "novel_title": novel_title,
                "chapter": chapter,
                "priority": 1 if chapter >= 800 else 5,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "attempts": 0,
                "last_error": None,
            })
            existing_ids.add(ch_wr_id)
            added += 1

    _save_queue(queue)
    log.info(f"discover 완료: {added}개 추가 (총 {len(all_chapters)}개 발견)")
    return added


# === 2단계: COLLECT — 큐 소비 → JSON 저장 ===

def run_collect(limit: int = 0) -> dict:
    """큐에서 wr_id를 하나씩 꺼내 fetch → JSON 저장.
    limit: 최대 처리할 챕터 수 (0=무제한)
    """
    from services.bookto31 import fetch_chapter, parse_chapter_body

    queue = _load_queue()
    if not queue:
        return {"processed": 0, "errors": [], "remaining": 0}

    processed = 0
    errors = []
    max_run = limit if limit > 0 else len(queue)

    for i in range(min(max_run, len(queue))):
        item = queue[i]
        wr_id = item['wr_id']
        novel_title = item.get('novel_title', '')
        item['attempts'] = item.get('attempts', 0) + 1

        log.info(f"[{i+1}/{len(queue)}] wr_id={wr_id} ({novel_title}) 시도 {item['attempts']}/3")

        # collect: HTML fetch → 본문 추출
        success, body = False, ""
        for attempt in range(3):
            try:
                html = fetch_chapter(wr_id)
                if not html:
                    time.sleep(2)
                    continue
                body = parse_chapter_body(html)
                if body and len(body) > 100:
                    success = True
                    break
            except Exception as e:
                log.warning(f"  fetch 실패: {e}")
                time.sleep(2)

        if not success:
            item['last_error'] = f"3회 시도 후 실패 (body={len(body) if body else 0})"
            log.warning(f"  ✗ {item['last_error']}")
            if item['attempts'] >= 3:
                queue = [q for q in queue if q['wr_id'] != wr_id]
            errors.append({"wr_id": wr_id, "error": item['last_error']})
            continue

        # 저장 (enrich/index 없이 순수 저장)
        chapter_num = item.get('chapter') or _extract_chapter_from_html(html)
        _save_chapter_only(novel_title, wr_id, body, chapter_num)
        log.info(f"  ✓ wr_id={wr_id} 저장 완료 ({len(body)} chars)")

        # 큐에서 제거
        queue = [q for q in queue if q['wr_id'] != wr_id]
        processed += 1

        # 다음 챕터 전 대기
        if i < len(queue) - 1 and limit != 1:
            log.info(f"  {CHAPTER_DELAY_SEC}초 대기...")
            time.sleep(CHAPTER_DELAY_SEC)

    _save_queue(queue)
    log.info(f"collect 완료: {processed}개 처리, {len(queue)}개 남음")
    return {"processed": processed, "errors": errors, "remaining": len(queue)}


# === 3단계: ENRICH — namu.wiki 메타데이터 보강 ===

def run_enrich(novel_id: Optional[str] = None) -> dict:
    """meta.json에 namu.wiki 메타데이터 보강 (1회만, namu_attempted 플래그로 관리)."""
    from services.metadata_namu import get_metadata

    DATA_DIR = Path('/opt/ai_data/flaresolverr/novels')
    results = {"enriched": 0, "skipped": 0, "errors": 0}

    targets = [DATA_DIR / novel_id] if novel_id else sorted(DATA_DIR.iterdir())
    for novel_dir in targets:
        if not novel_dir.is_dir():
            continue
        meta_file = novel_dir / 'meta.json'
        if not meta_file.exists():
            continue

        with open(meta_file) as f:
            meta = json.load(f)

        if meta.get('namu_attempted') or meta.get('author', '') != '미상':
            results['skipped'] += 1
            continue

        # namu_attempted 플래그 설정 (1회만)
        meta['namu_attempted'] = True
        with open(meta_file, 'w') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        log.info(f"enrich 시도: {novel_dir.name}")
        try:
            namu_meta = get_metadata(
                meta.get('title', novel_dir.name),
                download_cover_to=Path(f'/opt/ai_data/flaresolverr/covers/{novel_dir.name}.webp'),
            )
            if namu_meta:
                from lib.storage import update_meta_from_namu
                update_meta_from_namu(novel_dir.name, namu_meta)
                results['enriched'] += 1
                log.info(f"  ✓ {novel_dir.name}: 작가={namu_meta.get('author','?')}")
            else:
                log.info(f"  - {novel_dir.name}: namu.wiki 정보 없음")
        except Exception as e:
            results['errors'] += 1
            log.warning(f"  ✗ {novel_dir.name}: {e}")

    log.info(f"enrich 완료: {results['enriched']}개 보강, {results['skipped']}개 스킵")
    return results


# === 4단계: INDEX — _chapters_index.json 재구축 ===

def run_index(novel_id: Optional[str] = None) -> dict:
    """챕터 인덱스 캐시 재구축."""
    from services.data import rebuild_chapters_index, DATA_DIR

    results = {"indexed": 0, "errors": 0}
    targets = [DATA_DIR / novel_id] if novel_id else sorted(DATA_DIR.iterdir())

    for novel_dir in targets:
        if not novel_dir.is_dir():
            continue
        try:
            chapters = rebuild_chapters_index(novel_dir)
            results['indexed'] += 1
            log.info(f"  {novel_dir.name}: {len(chapters)}개 인덱싱")
        except Exception as e:
            results['errors'] += 1
            log.warning(f"  ✗ {novel_dir.name}: {e}")

    log.info(f"index 완료: {results['indexed']}개 인덱싱")
    return results


# === 5단계: REVALIDATE — Vercel ISR 캐시 갱신 ===

def run_revalidate(novel_id: Optional[str] = None) -> dict:
    """Vercel ISR 캐시 갱신."""
    import os as _os
    import requests as _requests

    url = _os.getenv("VERCEL_REVALIDATE_URL", "").strip()
    token = _os.getenv("VERCEL_REVALIDATE_TOKEN", "").strip()

    if not url or not token:
        log.info("revalidate: VERCEL_REVALIDATE_URL/TOKEN 미설정, 스킵")
        return {"revalidated": 0, "skipped": 1}

    paths = ["/"]
    if novel_id:
        paths.append(f"/novel/{novel_id}")

    try:
        resp = _requests.post(url, json={"paths": paths, "tags": ["novels"]},
                              headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if resp.ok:
            log.info(f"  ✓ Vercel revalidate: {paths}")
        else:
            log.warning(f"  ⚠ revalidate 실패: {resp.status_code}")
    except Exception as e:
        log.warning(f"  ⚠ revalidate 오류: {e}")

    return {"revalidated": 1}


# === 유틸 ===

def _load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_queue(queue: list) -> None:
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def _save_chapter_only(novel_title: str, wr_id: int, body: str, chapter_num: Optional[int] = None) -> bool:
    """순수 저장 (enrich/index/revalidate 없이)."""
    from lib.storage import save_chapter as _save

    save_kwargs = {"source": "bookto31"}
    if chapter_num is not None:
        save_kwargs["chapter_num"] = chapter_num
    return _save(novel_title, wr_id, body, **save_kwargs)


def _extract_chapter_from_html(html: str) -> Optional[int]:
    import re
    m = re.search(r'<title>(.*?)\s*-\s*(\d+)\s*(?:화|편|장)', html)
    if m:
        return int(m.group(2))
    m = re.search(r'<meta property="og:title" content="([^"]*?\s*-\s*(\d+)\s*(?:화|편|장))"', html)
    if m:
        return int(m.group(2))
    return None


# === 메인 ===

def run_all(novel_main_wr_id: int, novel_title: str) -> dict:
    """전체 파이프라인 실행 (discover → collect → enrich → index → revalidate)."""
    results = {}

    log.info("=" * 50)
    log.info("파이프라인 시작")
    log.info("=" * 50)

    # 1. discover
    log.info("\n[1/5] DISCOVER — 회차 발견")
    added = run_discover(novel_main_wr_id, novel_title)
    results['discover'] = added

    if added == 0:
        log.info("발견된 회차 없음, 종료")
        return results

    # 2. collect (1개만 먼저 처리해서 enrich용 meta.json 생성)
    log.info("\n[2/5] COLLECT — 1차 수집 (meta.json 생성용)")
    first = run_collect(limit=1)
    results['collect_first'] = first

    # 3. enrich
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    log.info("\n[3/5] ENRICH — 메타데이터 보강")
    enriched = run_enrich(novel_id)
    results['enrich'] = enriched

    # 4. index
    log.info("\n[4/5] INDEX — 인덱스 캐시 재구축")
    indexed = run_index(novel_id)
    results['index'] = indexed

    # 5. 나머지 collect
    log.info("\n[5/5] COLLECT — 나머지 수집")
    rest = run_collect(limit=0)
    results['collect_rest'] = rest

    # revalidate (마지막)
    log.info("\n[5/5] REVALIDATE — 캐시 갱신")
    revalidated = run_revalidate(novel_id)
    results['revalidate'] = revalidated

    log.info("\n" + "=" * 50)
    log.info("파이프라인 완료")
    log.info("=" * 50)
    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    if cmd == "discover":
        if len(sys.argv) < 3:
            print("사용법: pipeline.py discover <wr_id> [novel_title] [max_pages]")
            return 1
        wr_id = int(sys.argv[2])
        title = sys.argv[3] if len(sys.argv) > 3 else ""
        pages = int(sys.argv[4]) if len(sys.argv) > 4 else 50
        run_discover(wr_id, title, pages)

    elif cmd == "collect":
        limit = 0
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        run_collect(limit=limit)

    elif cmd == "enrich":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_enrich(novel_id)

    elif cmd == "index":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_index(novel_id)

    elif cmd == "revalidate":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_revalidate(novel_id)

    elif cmd == "all":
        if len(sys.argv) < 4:
            print("사용법: pipeline.py all <wr_id> <novel_title>")
            return 1
        wr_id = int(sys.argv[2])
        title = sys.argv[3]
        run_all(wr_id, title)

    elif cmd == "loop":
        """collect → index → revalidate 무한 루프 (5분 간격)."""
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        log.info("=" * 50)
        log.info("파이프라인 루프 시작 (Ctrl+C로 중단)")
        log.info("=" * 50)
        cycle = 0
        try:
            while True:
                cycle += 1
                log.info(f"\n--- Cycle {cycle} ---")

                # collect (1개씩)
                result = run_collect(limit=1)
                if result['processed'] == 0 and result['remaining'] == 0:
                    log.info("큐 비어 있음, 루프 종료")
                    break

                # index (해당 소설만)
                if novel_id:
                    run_index(novel_id)

                # revalidate (해당 소설만)
                if novel_id:
                    run_revalidate(novel_id)

                log.info(f"--- Cycle {cycle} 완료 (남은 작업: {result['remaining']}) ---")

                # 큐가 비었으면 종료
                remaining = _load_queue()
                if not remaining:
                    log.info("모든 작업 완료, 루프 종료")
                    break

                log.info(f"  {CHAPTER_DELAY_SEC}초 대기...")
                time.sleep(CHAPTER_DELAY_SEC)
        except KeyboardInterrupt:
            log.info("루프 중단 (사용자 요청)")

    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())