#!/usr/bin/env python3
"""ebooklib 워처 - 큐 기반 북토끼 챕터 수집 워커.

watchdog.py가 큐를 관리하고 ebook_worker.py가 작업을 수행.
- 큐: /opt/ai_data/flaresolverr/ebook_watcher/queue.json
  [
    {"wr_id": 25575, "novel_title": "...", "priority": 1, "added_at": "..."},
    ...
  ]
- 상태: /opt/ai_data/flaresolverr/ebook_watcher/status.json
  {"last_run": "...", "next_run": "...", "processed": 5, "errors": [...]}

워치독 패턴 - ebook_worker를 systemd service로 상주시켜 주기적 실행.

Rate Limiting:
- 챕터 사이 5분 안전 지연 (북토끼 정책)
- FlareSolverr 안정성 확보 (svc.pod 의존)
- 같은 챕터 중복 요청 방지
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# ebooklib 백엔드 경로 추가
sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')



WATCHER_DIR = Path('/opt/ai_data/flaresolverr/ebook_watcher')
WATCHER_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE = WATCHER_DIR / 'queue.json'
STATUS_FILE = WATCHER_DIR / 'status.json'
LOG_FILE = WATCHER_DIR / 'watcher.log'
LOCK_FILE = WATCHER_DIR / 'worker.lock'

# 챕터 간 안전 지연 (북토끼가 클라이언트로 인식하지 않도록)
CHAPTER_DELAY_SEC = 300  # 5분
# 같은 URL에 다시 요청 시 대기 시간
URL_RETRY_DELAY_SEC = 120  # 2분

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('ebook_watcher')


def load_queue() -> list:
    """큐 파일에서 작업 목록 로드."""
    if not QUEUE_FILE.exists():
        return []
    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_queue(queue: list) -> None:
    """큐 파일에 작업 목록 저장."""
    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def update_status(data: dict) -> None:
    """상태 파일 업데이트."""
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_status() -> dict:
    """상태 파일 로드."""
    if not STATUS_FILE.exists():
        return {}
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def add_chapter(wr_id: int, novel_title: str = "", priority: int = 5) -> None:
    """큐에 챕터 추가. 중복 제거."""
    queue = load_queue()
    if any(item['wr_id'] == wr_id for item in queue):
        log.info(f"wr_id={wr_id} 이미 큐에 있음 (스킵)")
        return

    queue.append({
        "wr_id": wr_id,
        "novel_title": novel_title,
        "priority": priority,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 0,
        "last_error": None,
    })
    save_queue(queue)
    log.info(f"큐 추가: wr_id={wr_id} ({novel_title})")


def is_lock_held() -> bool:
    """워커 락 확인 (동시 실행 방지)."""
    if not LOCK_FILE.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(LOCK_FILE.stat().st_mtime, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - mtime).total_seconds()
        # 락이 30분 이상 오래됐으면 stale로 간주
        if age > 1800:
            log.warning(f"Stale 락 발견 ({age:.0f}초 경과), 제거")
            LOCK_FILE.unlink()
            return False
        return True
    except Exception:
        return False


def acquire_lock() -> bool:
    """락 획득."""
    if is_lock_held():
        return False
    LOCK_FILE.write_text(f"pid={os.getpid()}\nstarted={datetime.now(timezone.utc).isoformat()}\n")
    return True


def release_lock() -> None:
    """락 해제."""
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def fetch_with_retry(wr_id: int, max_retries: int = 3, target_chapter: int = None) -> tuple[bool, str, str]:
    """북토끼에서 챕터 본문 가져오기 (재시도 포함).

    작품 메인 페이지인 경우 자동으로 회차 wr_id를 찾아 본문 추출.

    Args:
        wr_id: 북토끼 wr_id
        max_retries: 재시도 횟수
        target_chapter: 작품 메인일 때 찾을 회차 번호 (None이면 1화)

    Returns:
        (성공여부, 본문, 에러메시지)
    """
    from services.bookto31 import (
        fetch_chapter, parse_chapter_body, is_novel_index_page,
        find_chapter_wr_id, extract_chapter_wr_ids_from_index,
    )

    for attempt in range(1, max_retries + 1):
        try:
            log.info(f"  시도 {attempt}/{max_retries}: wr_id={wr_id}")
            html = fetch_chapter(wr_id)  # rate_limit=True 자동
            if not html:
                log.warning(f"  fetch 실패 (None 반환)")
                if attempt < max_retries:
                    time.sleep(URL_RETRY_DELAY_SEC)
                continue

            # 작품 메인 페이지인 경우 - 회차 wr_id 찾아서 본문 추출
            if is_novel_index_page(html):
                # 메타데이터 추출
                meta = parse_novel_meta_safe(html)
                log.info(f"  작품 메인 페이지 감지: {meta.get('title', '?')}")
                # 회차 목록 추출
                chapter_list = extract_chapter_wr_ids_from_index(html)
                log.info(f"  작품 메인에서 {len(chapter_list)}개 회차 발견")
                # target_chapter 찾기
                if target_chapter is None:
                    target_chapter = 1
                actual_wr_id = find_chapter_wr_id(html, wr_id, target_chapter)
                if not actual_wr_id:
                    return False, "", f"회차 {target_chapter}를 작품 메인에서 찾을 수 없음"
                log.info(f"  {target_chapter}화 wr_id={actual_wr_id}로 다시 fetch")
                # 찾은 wr_id로 다시 fetch (rate_limit 추가 안 됨, 같은 URL이 아니므로)
                html = fetch_chapter(actual_wr_id)
                if not html:
                    return False, "", f"회차 fetch 실패"

            if not html:
                continue

            body = parse_chapter_body(html)
            if body and len(body) > 100:
                return True, body, ""
            else:
                log.warning(f"  본문 파싱 실패 (len={len(body) if body else 0})")
                if attempt < max_retries:
                    time.sleep(URL_RETRY_DELAY_SEC)
                continue
        except Exception as e:
            log.error(f"  예외: {type(e).__name__}: {e}")
            if attempt < max_retries:
                time.sleep(URL_RETRY_DELAY_SEC)

    return False, "", f"{max_retries}회 시도 후 실패"


def parse_novel_meta_safe(html: str) -> Dict:
    """bookto31의 parse_novel_meta 안전 호출."""
    try:
        from services.bookto31 import parse_novel_meta
        return parse_novel_meta(html)
    except Exception:
        return {}


_BOOKTO31_LAST_CHECK = 0
_BOOKTO31_LAST_OK = True
BOOKTO31_CHECK_INTERVAL = 600  # 10분마다 health check


def _check_bookto31_alive() -> bool:
    """북토끼 health check (캐시: 10분마다 한 번만).

    bookto31._fetch_with_flaresolverr(rate_limit=False)를 사용해
    Cloudflare 우회 후 판단. rate_limit=False: health check는
    실제 데이터 수집이 아니므로 rate limiter를 우회한다.
    """
    global _BOOKTO31_LAST_CHECK, _BOOKTO31_LAST_OK
    now = time.time()
    if now - _BOOKTO31_LAST_CHECK < BOOKTO31_CHECK_INTERVAL:
        return _BOOKTO31_LAST_OK
    _BOOKTO31_LAST_CHECK = now

    try:
        from services import bookto31
        html = bookto31._fetch_with_flaresolverr(bookto31.BASE_URL + "/", rate_limit=False)
        _BOOKTO31_LAST_OK = html is not None and len(html) > 1000
    except Exception:
        _BOOKTO31_LAST_OK = False

    if not _BOOKTO31_LAST_OK:
        log.error("북토끼 health check 실패! 챕터 수집 중단")
    return _BOOKTO31_LAST_OK


def save_chapter(wr_id: int, novel_title: str, body: str) -> bool:
    """챕터 본문을 lib.storage로 저장 + Neon DB/Vercel 갱신."""
    from lib.storage import save_chapter as _save, get_novel_dir, update_meta_from_namu

    # lib.storage로 JSON 저장 + meta.json 갱신
    if not _save(novel_title, wr_id, body, source="bookto31"):
        log.error(f"  ✗ 챕터 저장 실패: wr_id={wr_id}")
        return False

    novel_id = novel_title.replace(' ', '_').replace('/', '_') if novel_title else f"novel_{wr_id}"
    novel_dir = get_novel_dir(novel_title)

    # 새 소설이면 namu.wiki 메타데이터 보강
    meta_file = novel_dir / 'meta.json'
    if meta_file.exists():
        import json as _json
        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = _json.load(f)
        if meta.get('author') == '미상':
            namu_meta = enrich_metadata_from_namu(novel_id, novel_title)
            if namu_meta:
                update_meta_from_namu(novel_title, namu_meta)

    log.info(f"  저장 완료: wr_id={wr_id} ({len(body)} chars)")

    # Neon DB 동기화 (Vercel SSR용)
    try:
        import sys
        sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')
        from services.ebook_sync import sync_novel
        if sync_novel(novel_id, novel_dir):
            log.info(f"  ✓ Neon DB 동기화 완료")
    except Exception as e:
        log.warning(f"  ⚠ Neon 동기화 실패 (계속 진행): {e}")

    # Vercel 캐시 즉시 갱신 트리거
    _trigger_vercel_revalidate(novel_id)

    return True


def _trigger_vercel_revalidate(novel_id: str) -> None:
    """Vercel ISR 캐시 즉시 갱신.

    새 챕터 저장 시 Vercel의 revalidate endpoint 호출.
    환경변수:
    - VERCEL_REVALIDATE_URL: 예) https://miniebook.vercel.app/api/revalidate
    - VERCEL_REVALIDATE_TOKEN: Vercel 측 env에 설정한 토큰
    """
    import os
    import requests

    url = os.getenv("VERCEL_REVALIDATE_URL", "").strip()
    token = os.getenv("VERCEL_REVALIDATE_TOKEN", "").strip()

    if not url or not token:
        # 설정 없으면 조용히 스킵 (devforge 단독 환경 등)
        return

    # 갱신 대상 path
    paths = ["/", f"/novel/{novel_id}"]
    tags = ["novels"]

    try:
        resp = requests.post(
            url,
            json={"paths": paths, "tags": tags},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.ok:
            log.info(f"  ✓ Vercel revalidate 성공: {paths}")
        else:
            log.warning(f"  ⚠ Vercel revalidate 실패: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.warning(f"  ⚠ Vercel revalidate 오류 (무시): {e}")


def enrich_metadata_from_namu(novel_id: str, novel_title: str) -> dict:
    """namu.wiki에서 소설 메타데이터 가져와서 meta.json 생성.

    Returns:
        meta.json 내용 dict
    """
    import json as _json

    # 기본 메타
    meta = {
        "id": novel_id,
        "title": novel_title,
        "author": "미상",
        "totalChapters": 1,
        "coverUrl": None,
        "description": "",
        "genre": [],
        "status": "unknown",
        "publisher": "북토끼",
        "namuUrl": None,
    }

    # 표지 이미지 저장 경로
    cover_path = Path('/opt/ai_data/flaresolverr/covers') / f"{novel_id}.webp"

    # 첫 챕터 저장 시 namu.wiki 조회 (rate_limit 자동 적용)
    try:
        from services import metadata_namu
        log.info(f"  namu.wiki 메타데이터 조회 시도: {novel_title}")
        namu_meta = metadata_namu.get_metadata(novel_title, download_cover_to=cover_path)
        if namu_meta:
            # lib.storage로 meta.json 업데이트
            from lib.storage import update_meta_from_namu
            update_meta_from_namu(novel_title, namu_meta)
            meta.update(namu_meta)
            log.info(f"  ✓ namu.wiki 메타데이터 적용: 작가={meta.get('author', '?')}, 표지={meta.get('coverUrl', '?')}")
    except Exception as e:
        log.warning(f"  namu.wiki 메타데이터 조회 실패 (기본값 사용): {e}")

    return meta


def process_queue() -> dict:
    """큐의 모든 챕터를 순차 처리 (안전 지연 포함)."""
    # 북토끼 health check (죽었으면 조기 종료)
    if not _check_bookto31_alive():
        log.error("북토끼 다운! 대안 소스 또는 북토끼 복구 대기 필요")
        return {"processed": 0, "errors": ["bookto31_down"]}

    queue = load_queue()
    if not queue:
        log.info("큐 비어 있음")
        return {"processed": 0, "errors": []}

    # 우선순위 순 정렬 (낮은 숫자가 높은 우선순위)
    queue.sort(key=lambda x: (x.get('priority', 5), x.get('added_at', '')))

    processed = 0
    errors = []

    for i, item in enumerate(queue):
        wr_id = item['wr_id']
        novel_title = item.get('novel_title', '')

        log.info(f"\n=== 처리 [{i+1}/{len(queue)}]: wr_id={wr_id} ({novel_title}) ===")

        # 챕터 가져오기 (재시도 포함)
        success, body, error = fetch_with_retry(wr_id)
        if success:
            # DB 저장
            if save_chapter(wr_id, novel_title, body):
                processed += 1
                log.info(f"  ✓ wr_id={wr_id} 처리 완료")
            else:
                errors.append({"wr_id": wr_id, "error": "DB 저장 실패"})
        else:
            errors.append({"wr_id": wr_id, "error": error})
            # 재시도 카운트 증가
            item['attempts'] = item.get('attempts', 0) + 1
            item['last_error'] = error
            if item['attempts'] >= 5:
                log.error(f"  ✗ wr_id={wr_id} 5회 실패, 큐에서 제거")
            else:
                log.warning(f"  ✗ wr_id={wr_id} 실패 (시도 {item['attempts']}/5)")

        # 다음 챕터 전 안전 지연 (마지막 챕터는 생략)
        if i < len(queue) - 1:
            log.info(f"  {CHAPTER_DELAY_SEC}초 안전 대기...")
            time.sleep(CHAPTER_DELAY_SEC)

    # 실패+재시도 가능한 챕터만 큐에 남김 (성공한 챕터는 제거)
    remaining = [item for item in queue if any(
        e['wr_id'] == item['wr_id'] for e in errors
    ) and item.get('attempts', 0) < 5]
    save_queue(remaining)

    return {"processed": processed, "errors": errors, "remaining": len(remaining)}


def main():
    """워커 메인 루프."""
    log.info("=== ebook_watcher 워커 시작 ===")

    if not acquire_lock():
        log.info("다른 워커 실행 중. 종료.")
        sys.exit(0)

    try:
        status = load_status()
        result = process_queue()

        # 상태 업데이트
        status.update({
            "last_run": datetime.now(timezone.utc).isoformat(),
            "processed": result.get("processed", 0),
            "errors": result.get("errors", []),
            "remaining": result.get("remaining", 0),
            "queue_size": len(load_queue()),
        })
        update_status(status)

        log.info(f"\n=== 사이클 완료: 처리={result.get('processed', 0)}, "
                 f"에러={len(result.get('errors', []))}, "
                 f"남은 작업={result.get('remaining', 0)} ===")
    finally:
        release_lock()


if __name__ == "__main__":
    main()