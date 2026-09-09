#!/usr/bin/env python3
"""ebooklib 파이프라인 — 체인 방식 단계별 실행.

각 단계는 독립적으로 실행되며, 이전 단계의 결과물(파일)을 입력으로 받음.
한 단계가 실패해도 다음 단계에 영향 없음.

사용법:
  python3 scripts/pipeline.py discover <wr_id> [novel_title] [max_pages] [--source bookto31|newtoki]
  python3 scripts/pipeline.py collect [--limit N] [--source bookto31|newtoki]
  python3 scripts/pipeline.py enrich [novel_id]
  python3 scripts/pipeline.py index [novel_id]
  python3 scripts/pipeline.py revalidate [novel_id]
  python3 scripts/pipeline.py epub [novel_id ...]    # EPUB 캐시 제작/재제작 (인자 없으면 전체)
  python3 scripts/pipeline.py all <wr_id> [novel_title] [--source bookto31|newtoki]
  python3 scripts/pipeline.py loop <novel_title> [--source bookto31|newtoki]  # 자동 루프
"""

import json
import os
import sys
import time
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

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
PID_FILE = WATCHER_DIR / 'pipeline.pid'
DLQ_FILE = WATCHER_DIR / 'failed.json'
CHAPTER_DELAY_SEC = 300


_SD_NOTIFY_READY = False


def _sd_notify(state: str = "") -> None:
    """systemd watchdog 신호 전송 (Type=notify + WatchdogSec 대응).

    loop이 5분 간격으로 사이클을 돌 때 WATCHDOG=1 신호를 보내,
    systemd가 프로세스 hang 여부를 감지한다.
    첫 호출 시 READY=1을 함께 보내 서비스 시작을 알린다.
    CLI 실행(NOTIFY_SOCKET 없음)은 무시.
    """
    global _SD_NOTIFY_READY
    try:
        sock_path = os.environ.get("NOTIFY_SOCKET")
        if not sock_path:
            return
        import socket
        msg = ""
        if not _SD_NOTIFY_READY:
            msg += "READY=1\n"
            _SD_NOTIFY_READY = True
        msg += "WATCHDOG=1\n"
        if state:
            msg += f"STATUS={state}\n"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(sock_path)
        sock.send(msg.encode())
        sock.close()
    except Exception:
        pass  # watchdog 신호 실패는 치명적이지 않음


def _write_status(data: dict) -> None:
    """진행 상황을 status.json에 기록 (loop/collect가 주기적으로 호출)."""
    try:
        data = dict(data)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning(f"status.json 기록 실패: {e}")


def _add_to_dlq(item: dict, error: str) -> None:
    """실패한 항목을 DLQ(failed.json)에 기록 — 데이터 손실 방지.

    3회 시도 후 실패한 항목은 queue에서 제거되지만, 재시도/분석을 위해
    failed.json에 보존한다.
    """
    try:
        records = []
        if DLQ_FILE.exists():
            try:
                with open(DLQ_FILE, encoding='utf-8') as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    records = []
            except (json.JSONDecodeError, OSError):
                records = []
        records.append({
            "wr_id": item.get('wr_id'),
            "novel_title": item.get('novel_title'),
            "source": item.get('source', 'bookto31'),
            "chapter": item.get('chapter'),
            "error": error,
            "attempts": item.get('attempts'),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        # 최대 5000개 유지 (무한 증가 방지)
        if len(records) > 5000:
            records = records[-5000:]
        with open(DLQ_FILE, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"DLQ 기록 실패: {e}")

# ============================================================
# 수집기 레지스트리 — source별 collector 분기
# ============================================================

def _collect_bookto31(wr_id: int, item: dict) -> tuple[bool, str, str, Optional[int]]:
    """bookto31 수집기: FlareSolverr + GNUBOARD5 본문 파싱."""
    from services.bookto31 import fetch_chapter, parse_chapter_body
    html = fetch_chapter(wr_id)
    if not html:
        return False, "", "fetch 실패", None
    body = parse_chapter_body(html)
    if not body or len(body) < 100:
        return False, body, f"본문 부족 ({len(body)} chars)", None
    chapter_num = _extract_chapter_from_html(html)
    return True, body, "", chapter_num


def _collect_newtoki(wr_id: int, item: dict) -> tuple[bool, str, str, Optional[int]]:
    """newtoki 수집기: Playwright + DataImpulse + AES-GCM 복호화."""
    from lib.toki31_playwright import fetch_chapter_content_full
    novel_id = item.get('novel_ref', '')
    if not novel_id:
        return False, "", "novel_ref 필요 (newtoki는 novel_id+episode_id 필요)", None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(fetch_chapter_content_full(novel_id, wr_id))
        return True, result, "", None
    except Exception as e:
        return False, "", f"newtoki fetch 실패: {e}", None
    finally:
        loop.close()


COLLECTORS: dict[str, Callable] = {
    "bookto31": _collect_bookto31,
    "newtoki": _collect_newtoki,
    "toki31": _collect_newtoki,  # alias
}


def _parse_source() -> str:
    """CLI 인자에서 --source 추출."""
    for i, arg in enumerate(sys.argv):
        if arg == "--source" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "bookto31"

# === 1단계: DISCOVER — wr_id 발견 → 큐에 추가 ===

def run_discover(wr_id: int, novel_title: str = "", max_pages: int = 50, source: str = "bookto31", dry_run: bool = False) -> int:
    """북토끼 작품 메인에서 모든 회차 wr_id 발견 → 큐에 추가.

    dry_run: 첫 페이지만 fetch해서 제목 추출 후 출력하고 종료.
    """
    from services.bookto31 import extract_chapter_wr_ids_from_index
    from lib.flaresolverr_client import FlareSolverrSession

    fs = FlareSolverrSession(rate_limit=False)
    all_chapters = []
    seen = set()
    title = ""

    # dry_run: 첫 페이지만 fetch해서 제목 추출
    if dry_run:
        url = f"https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={wr_id}&epage=1"
        html = fs.fetch(url)
        if html:
            import re as _re
            title_m = _re.search(r"<title>(.*?)</title>", html)
            if title_m:
                title = title_m.group(1).strip()
                title = _re.sub(r"\s*[-–|]\s*(?:북토끼|bookto31).*", "", title).strip()
            if not title:
                og_m = _re.search(r'<meta property="og:title" content="([^"]+)"', html)
                if og_m:
                    title = og_m.group(1).strip()
        print(f"TITLE:{title or novel_title or f'소설 {wr_id}'}")
        return 0

    # epage 파라미터로 페이지네이션 (select 드롭다운 회차 목록)
    # 화산귀환 등 일부 작품은 spage로 페이징되므로 둘 다 시도
    # 전체 회차를 확인하기 위해 max_pages 상한을 크게 잡고, 회차가 없으면 자동 중단
    max_pages = max(max_pages, 200)
    for page_param in ("epage", "spage"):
        page_seen = set()
        for page in range(1, max_pages + 1):
            url = f"https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={wr_id}&{page_param}={page}"
            html = fs.fetch(url)

            # 첫 페이지에서 제목 추출
            if page == 1 and html and not title:
                import re as _re
                title_m = _re.search(r"<title>(.*?)</title>", html)
                if title_m:
                    title = title_m.group(1).strip()
                    title = _re.sub(r"\s*[-–|]\s*(?:북토끼|bookto31).*", "", title).strip()
                if not title:
                    og_m = _re.search(r'<meta property="og:title" content="([^"]+)"', html)
                    if og_m:
                        title = og_m.group(1).strip()
            if not html or len(html) < 1000:
                log.info(f"  {page_param}={page}: 응답 없음, 중단")
                break

            page_chapters = extract_chapter_wr_ids_from_index(html)
            if not page_chapters:
                log.info(f"  {page_param}={page}: 회차 없음, 중단")
                break

            new = 0
            for ch_wr_id, chapter in page_chapters:
                if ch_wr_id not in seen and ch_wr_id != wr_id:
                    seen.add(ch_wr_id)
                    all_chapters.append((ch_wr_id, chapter))
                    page_seen.add(ch_wr_id)
                    new += 1
            log.info(f"  {page_param}={page}: {new}개 신규 (누적 {len(all_chapters)})")
            if new == 0 and page > 1:
                break
            # 같은 페이지가 반복되면 (epage를 무시하는 작품) 다음 파라미터로
            if new == 0 and page >= 1 and page_seen and all(c in page_seen for c, _ in page_chapters):
                log.info(f"  {page_param}={page}: 중복 페이지, 중단")
                break

    # 큐에 추가 (queue에 이미 있거나, 파일로 이미 저장된 회차는 제외)
    queue = _load_queue()
    existing_ids = {item['wr_id'] for item in queue}
    # 이미 저장된 회차 (동일 작품 디렉토리의 wr_id.json)
    saved_ids = set()
    try:
        novel_id_dir = novel_title.replace(' ', '_').replace('/', '_') if novel_title else f"novel_{wr_id}"
        novel_dir = Path('/opt/ai_data/flaresolverr/novels') / novel_id_dir
        if novel_dir.exists():
            for f in novel_dir.glob("*.json"):
                if f.name in ("meta.json", "_chapters_index.json"):
                    continue
                try:
                    saved_ids.add(int(f.stem))
                except ValueError:
                    pass
    except Exception:
        pass
    added = 0
    for ch_wr_id, chapter in all_chapters:
        if ch_wr_id in existing_ids or ch_wr_id in saved_ids:
            continue
        queue.append({
            "wr_id": ch_wr_id,
            "novel_title": novel_title,
            "chapter": chapter,
            "source": source,  # ← source 필드
            "priority": 1 if chapter >= 800 else 5,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
            "last_error": None,
        })
        existing_ids.add(ch_wr_id)
        added += 1

    _save_queue(queue)

    # 작품 메인 wr_id 기록 (loop의 자동 discover를 위해 meta.json에 저장)
    if novel_title:
        try:
            novel_id_dir = novel_title.replace(' ', '_').replace('/', '_')
            novel_dir = Path('/opt/ai_data/flaresolverr/novels') / novel_id_dir
            novel_dir.mkdir(parents=True, exist_ok=True)
            meta_file = novel_dir / 'meta.json'
            meta = {}
            if meta_file.exists():
                try:
                    with open(meta_file, encoding='utf-8') as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            meta['main_wr_id'] = wr_id
            meta['source'] = source
            meta['title'] = novel_title
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"meta.json main_wr_id 기록 실패: {e}")

    log.info(f"discover 완료: {added}개 추가 (총 {len(all_chapters)}개 발견, 저장됨 {len(saved_ids)}개 스킵, source={source})")
    return added


# === 2단계: COLLECT — 큐 소비 → JSON 저장 ===

def run_collect(limit: int = 0, source_filter: str = "") -> dict:
    """큐에서 wr_id를 하나씩 꺼내 source별 collector로 fetch → JSON 저장.
    limit: 최대 처리할 챕터 수 (0=무제한)
    source_filter: 특정 source만 처리 (빈 문자열=전체)

    단일 writer 락: 두 프로세스(예: bookto31 loop + toki31 collect)가 동시에
    queue를 처리하지 못하도록 전체 트랜잭션을 직렬화한다.
    """
    _acquire_collect_lock()
    try:
        return _run_collect_locked(limit, source_filter)
    finally:
        _release_collect_lock()


def _run_collect_locked(limit: int = 0, source_filter: str = "") -> dict:
    """run_collect 본체 (락 보유 상태에서 실행)."""
    full_queue = _load_queue()
    if not full_queue:
        return {"processed": 0, "errors": [], "remaining": 0}

    # source 필터 (처리 대상만 선택. 전체 queue는 유지)
    if source_filter:
        queue = [item for item in full_queue if item.get('source', 'bookto31') == source_filter]
    else:
        queue = list(full_queue)

    if not queue:
        return {"processed": 0, "errors": [], "remaining": 0}

    processed = 0
    errors = []
    max_run = limit if limit > 0 else len(queue)
    total = len(queue)
    # 처리/제거된 wr_id 추적 (전체 queue에서 제거)
    removed_ids = set()
    # 수집이 끝난 소설 감지용 (novel_id → 제목)
    touched_novels: dict[str, str] = {}

    for i in range(min(max_run, len(queue))):
        item = queue[i]
        wr_id = item['wr_id']
        novel_title = item.get('novel_title', '')
        source = item.get('source', 'bookto31')
        item['attempts'] = item.get('attempts', 0) + 1

        # 진행 상황 기록 (현재 처리 중인 회차)
        _write_status({
            "phase": "collect",
            "current": {
                "wr_id": wr_id,
                "novel_title": novel_title,
                "chapter": item.get('chapter'),
                "source": source,
                "attempt": item['attempts'],
            },
            "index": i + 1,
            "total": total,
            "remaining": total - i - 1,
            "processed": processed,
        })

        log.info(f"[{i+1}/{len(queue)}] wr_id={wr_id} ({novel_title}) source={source} 시도 {item['attempts']}/3")

        # collector 선택 (source별 분기)
        collector = COLLECTORS.get(source)
        if not collector:
            log.warning(f"  ✗ 알 수 없는 source: {source}")
            errors.append({"wr_id": wr_id, "error": f"Unknown source: {source}"})
            removed_ids.add(wr_id)
            continue

        # 3회 재시도 (fetch 시간 측정 → 적응형 딜레이)
        success, body, error_msg, chapter_num = False, "", "", None
        fetch_elapsed = 0.0
        _t0 = time.monotonic()
        for attempt in range(3):
            try:
                success, body, error_msg, chapter_num = collector(wr_id, item)
                if success:
                    break
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                log.warning(f"  fetch 실패 ({attempt+1}/3): {error_msg}")
                time.sleep(2)
        fetch_elapsed = time.monotonic() - _t0

        if not success:
            item['last_error'] = f"3회 시도 후 실패 (body={len(body) if body else 0})"
            log.warning(f"  ✗ {item['last_error']}")
            if item['attempts'] >= 3:
                # 3회 실패 → DLQ 기록 후 queue에서 제거 (데이터 보존)
                _add_to_dlq(item, item['last_error'])
                removed_ids.add(wr_id)
            errors.append({"wr_id": wr_id, "error": item['last_error']})
            continue

        # 저장 (enrich/index 없이 순수 저장)
        chapter_num = item.get('chapter') or chapter_num
        _save_chapter_only(novel_title, wr_id, body, chapter_num, source)
        _body_len = body[1] if isinstance(body, tuple) and len(body) == 2 else body
        log.info(f"  ✓ wr_id={wr_id} 저장 완료 ({len(_body_len)} chars)")

        # 큐에서 제거 (전체 queue 기준)
        removed_ids.add(wr_id)
        processed += 1
        if novel_title:
            touched_novels[novel_title.replace(' ', '_').replace('/', '_')] = novel_title

        # 다음 챕터 전 대기 — 적응형 딜레이 (업계 표준: 10 × fetch 시간)
        # bookto31: Cloudflare 차단 방지 위해 최소 5분 / 최대 10분
        # toki31: 한번에 다 받는 형식이라 짧게 (5~60초)
        if len(queue) > 0 and limit != 1:
            if source == "toki31":
                delay = max(5.0, min(60.0, fetch_elapsed * 10))
            else:
                delay = max(300.0, min(600.0, fetch_elapsed * 10))
            log.info(f"  {delay:.0f}초 대기 (fetch {fetch_elapsed:.1f}s × 10)...")
            time.sleep(delay)

    # 전체 queue에서 처리/실패 제거된 항목만 제거하고 저장
    remaining_queue = [q for q in full_queue if q['wr_id'] not in removed_ids]
    _save_queue(remaining_queue)
    log.info(f"collect 완료: {processed}개 처리, {len(remaining_queue)}개 남음")

    # EPUB 제작/재제작 — 이번에 queue가 비워진(전체 회차 수집 완료) 소설만
    _build_epub_for_drained_novels(remaining_queue, touched_novels)

    _write_status({
        "phase": "collect",
        "current": None,
        "index": total,
        "total": total,
        "remaining": len(remaining_queue),
        "processed": processed,
        "last_result": {"processed": processed, "errors": len(errors), "remaining": len(remaining_queue)},
    })
    return {"processed": processed, "errors": errors, "remaining": len(remaining_queue)}


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
    """큐 읽기 — 락 파일 공유 잠금으로 동시 읽기 안전."""
    if not QUEUE_FILE.exists():
        return []
    try:
        import fcntl
        lockf = _queue_lock()
        fcntl.flock(lockf, fcntl.LOCK_SH)
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError, ImportError):
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []


_QUEUE_LOCK_FILE = None
_COLLECT_LOCK_FILE = None


def _queue_lock():
    """queue.json 전용 락 파일 (fcntl 배타 잠금). 크로스 프로세스 직렬화."""
    global _QUEUE_LOCK_FILE
    if _QUEUE_LOCK_FILE is None:
        _QUEUE_LOCK_FILE = open(QUEUE_FILE.with_suffix('.lock'), 'w')
    return _QUEUE_LOCK_FILE


def _collect_lock():
    """run_collect 전용 락 파일 — queue.lock과 분리해 중첩 flock 무력화 방지.

    fcntl은 같은 프로세스에서 같은 fd에 flock을 걸면 이전 락을 대체한다.
    따라서 _acquire_collect_lock(LOCK_EX) 상태에서 _load_queue가 같은 fd에
    LOCK_SH를 걸면 collect 락이 다운그레이드/해제되는 심각한 버그가 있다.
    별도 파일로 분리해 이 문제를 해결한다.
    """
    global _COLLECT_LOCK_FILE
    if _COLLECT_LOCK_FILE is None:
        _COLLECT_LOCK_FILE = open(QUEUE_FILE.with_suffix('.collect.lock'), 'w')
    return _COLLECT_LOCK_FILE


def _acquire_collect_lock():
    """run_collect 전체 트랜잭션 락 — 두 프로세스가 동시에 queue를 처리하지 못하게."""
    import fcntl
    fcntl.flock(_collect_lock(), fcntl.LOCK_EX)
    return True


def _release_collect_lock():
    """run_collect 트랜잭션 락 해제."""
    import fcntl
    try:
        fcntl.flock(_collect_lock(), fcntl.LOCK_UN)
    except Exception:
        pass


def _save_queue(queue: list) -> None:
    """큐 저장 — 크로스 프로세스 배타 잠금 + atomic write (tmp → rename).

    동시 쓰기 시 read-modify-write race를 방지한다.
    (toki31/bookto31 루프가 서로의 항목을 덮어쓰던 버그 해결)
    각 프로세스는 전용 락 파일(queue.json.lock)을 사용해 직렬화하고,
    임시 파일도 프로세스/스레드별 고유 이름으로 생성해 충돌을 피한다.
    """
    import fcntl
    import threading as _threading
    lockf = _queue_lock()
    fcntl.flock(lockf, fcntl.LOCK_EX)
    try:
        # PID + 스레드 ID 조합으로 고유 tmp 파일 생성 (동시 쓰기 충돌 방지)
        tmp_path = QUEUE_FILE.with_suffix(f'.tmp.{os.getpid()}.{_threading.get_ident()}')
        with open(tmp_path, 'w') as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, QUEUE_FILE)  # atomic rename
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)


def _save_chapter_only(novel_title: str, wr_id: int, body: str, chapter_num: Optional[int] = None, source: str = "bookto31") -> bool:
    """순수 저장 (enrich/index/revalidate 없이).

    body가 튜플 (title, content)이면 (newtoki/toki31 collector) content만 사용.
    """
    from lib.storage import save_chapter as _save

    if isinstance(body, tuple) and len(body) == 2:
        # (title, content) → content 사용
        content = body[1]
        if chapter_num is None:
            from lib.storage import _extract_chapter_num
            chapter_num = _extract_chapter_num(content)
        body = content

    save_kwargs = {"source": source}
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


def _build_epub_for_drained_novels(remaining_queue: list, touched_novels: dict) -> None:
    """queue가 비워진(전체 회차 수집 완료) 소설의 EPUB을 제작/재제작.

    fingerprint 기반이라 새 회차가 없으면 no-op. 실패해도 파이프라인은 계속 진행.
    """
    if not touched_novels:
        return
    from services.epub import maybe_build_epub

    remaining_novel_ids = {
        q.get('novel_title', '').replace(' ', '_').replace('/', '_')
        for q in remaining_queue if q.get('novel_title')
    }
    for novel_id, title in touched_novels.items():
        if novel_id in remaining_novel_ids:
            # 아직 이 소설의 다른 회차가 queue에 남아 있음 → 아직 수집 중
            continue
        try:
            path = maybe_build_epub(novel_id)
            if path:
                log.info(f"  ✓ EPUB 제작/재제작 완료: {title} -> {path.name}")
            else:
                log.info(f"  - EPUB 빌드 스킵/실패: {title} (챕터 부족 또는 제작 실패)")
        except Exception as e:
            log.warning(f"  ⚠ EPUB 빌드 오류 ({title}): {type(e).__name__}: {e}")


# === 메인 ===

def run_all(novel_main_wr_id: int, novel_title: str, source: str = "bookto31") -> dict:
    """전체 파이프라인 실행 (discover → collect → enrich → index → revalidate)."""
    results = {}

    log.info("=" * 50)
    log.info(f"파이프라인 시작 (source={source})")
    log.info("=" * 50)

    # 1. discover
    log.info("\n[1/5] DISCOVER — 회차 발견")
    added = run_discover(novel_main_wr_id, novel_title, source=source)
    results['discover'] = added

    if added == 0:
        log.info("발견된 회차 없음, 종료")
        return results

    # 2. collect (1개만 먼저 처리해서 enrich용 meta.json 생성)
    log.info("\n[2/5] COLLECT — 1차 수집 (meta.json 생성용)")
    first = run_collect(limit=1, source_filter=source)
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
    rest = run_collect(limit=0, source_filter=source)
    results['collect_rest'] = rest

    # revalidate (마지막)
    log.info("\n[5/5] REVALIDATE — 캐시 갱신")
    revalidated = run_revalidate(novel_id)
    results['revalidate'] = revalidated

    log.info("\n" + "=" * 50)
    log.info("파이프라인 완료")
    log.info("=" * 50)
    return results


def _auto_discover(source: str = "bookto31") -> None:
    """저장된 연재작들의 새 회차를 주기적으로 discover.

    meta.json에 기록된 main_wr_id를 읽어 각 작품의 discover를 실행.
    queue에 없거나 이미 완결인 작품은 스킵.
    """
    novels_dir = Path('/opt/ai_data/flaresolverr/novels')
    if not novels_dir.exists():
        return
    for novel_dir in sorted(novels_dir.iterdir()):
        if not novel_dir.is_dir():
            continue
        meta_file = novel_dir / 'meta.json'
        if not meta_file.exists():
            continue
        try:
            with open(meta_file, encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:
            continue
        # 완결작은 새 회차 없음
        if meta.get('status') == '완결':
            continue
        main_wr_id = meta.get('main_wr_id')
        title = meta.get('title') or novel_dir.name.replace('_', ' ')
        if not main_wr_id:
            continue
        log.info(f"  auto-discover: {title} (main_wr_id={main_wr_id})")
        try:
            run_discover(int(main_wr_id), title, max_pages=200, source=source)
        except Exception as e:
            log.warning(f"  {title} discover 실패: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    if cmd == "discover":
        if len(sys.argv) < 3:
            print("사용법: pipeline.py discover <wr_id> [novel_title] [max_pages] [--source bookto31|newtoki] [--dry-run]")
            return 1
        wr_id = int(sys.argv[2])
        source = _parse_source()
        dry_run = "--dry-run" in sys.argv
        # title과 max_pages는 --source/--dry-run 이전의 위치 인자
        title = ""
        pages = 50
        positional = [a for a in sys.argv[3:] if not a.startswith("--")]
        if len(positional) > 0:
            title = positional[0]
        if len(positional) > 1:
            try:
                pages = int(positional[1])
            except ValueError:
                pass
        run_discover(wr_id, title, pages, source, dry_run)

    elif cmd == "collect":
        limit = 0
        source = ""
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
            if arg == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
        run_collect(limit=limit, source_filter=source)

    elif cmd == "enrich":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_enrich(novel_id)

    elif cmd == "index":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_index(novel_id)

    elif cmd == "revalidate":
        novel_id = sys.argv[2] if len(sys.argv) > 2 else None
        run_revalidate(novel_id)

    elif cmd == "epub":
        """EPUB 캐시 제작/재제작 (수동).
        사용법: pipeline.py epub [novel_id ...]   (인자 없으면 전체 소설)
        fingerprint 기반이라 변경 없으면 no-op.
        """
        from services.data import DATA_DIR
        from services.epub import maybe_build_epub
        targets = sys.argv[2:]
        built = 0
        for novel_dir in sorted(DATA_DIR.iterdir()):
            nid = novel_dir.name
            if not novel_dir.is_dir() or nid.startswith("."):
                continue
            if targets and nid not in targets:
                continue
            try:
                path = maybe_build_epub(nid, force=True)
                log.info(f"  {nid}: {'✓ 제작' if path else '- 스킵/실패'}")
                if path:
                    built += 1
            except Exception as e:
                log.warning(f"  {nid}: 오류 {type(e).__name__}: {e}")
        log.info(f"EPUB 캐시 제작 완료: {built}개")

    elif cmd == "all":
        if len(sys.argv) < 4:
            print("사용법: pipeline.py all <wr_id> <novel_title> [--source bookto31|newtoki]")
            return 1
        wr_id = int(sys.argv[2])
        title = sys.argv[3]
        source = _parse_source()
        run_all(wr_id, title, source)

    elif cmd == "loop":
        """collect → index → revalidate 무한 루프 (5분 간격).

        bookto31: 연재작 특성상 queue가 비어도 종료하지 않고 대기한다.
                  (URL/discover로 회차가 추가되면 계속 수집)
        newtoki/완결작: queue 소진 시 루프 종료.
        """
        novel_title = sys.argv[2] if len(sys.argv) > 2 else None
        source = _parse_source()
        log.info("=" * 50)
        log.info(f"파이프라인 루프 시작 (source={source}, novel={novel_title or '전체'}, Ctrl+C로 중단)")
        log.info("=" * 50)
        # PID 기록
        PID_FILE.write_text(str(os.getpid()))
        cycle = 0
        last_discover_day = None  # 이번 달에 discover 실행했는지 추적
        try:
            while True:
                cycle += 1
                log.info(f"\n--- Cycle {cycle} ---")
                _write_status({"phase": "loop", "cycle": cycle, "source": source})
                # systemd watchdog 신호 (WatchdogSec 대응)
                _sd_notify(f"cycle {cycle}")

                # bookto31: 매월 1일 1회 연재작 새 회차 감지 (discover)
                today = datetime.now().strftime("%Y-%m")
                if source == "bookto31" and today != last_discover_day:
                    if datetime.now().day == 1:
                        last_discover_day = today
                        log.info("discover: 매월 1일 연재작 새 회차 확인")
                        try:
                            _auto_discover(source)
                        except Exception as e:
                            log.warning(f"auto-discover 실패: {e}")
                    else:
                        # 1일이 아니면 이번 달 discover는 아직 안 함
                        pass

                # collect (1개씩, source 필터)
                result = run_collect(limit=1, source_filter=source)
                if result['processed'] == 0 and result['remaining'] == 0:
                    if source == "bookto31":
                        # bookto31 연재작: queue가 비어도 계속 대기 (새 회차 추가 대기)
                        log.info("큐 비어 있음 - bookto31은 새 회차 대기 중")
                        log.info(f"  {CHAPTER_DELAY_SEC}초 대기...")
                        time.sleep(CHAPTER_DELAY_SEC)
                        continue
                    log.info("큐 비어 있음, 루프 종료")
                    break

                # index (해당 소설만)
                if novel_title:
                    run_index(novel_title)

                # revalidate (해당 소설만)
                if novel_title:
                    run_revalidate(novel_title)

                log.info(f"--- Cycle {cycle} 완료 (남은 작업: {result['remaining']}) ---")

                # 큐가 비었으면 종료 (newtoki/완결작만)
                remaining = _load_queue()
                if not remaining:
                    if source == "bookto31":
                        log.info("bookto31: 모든 회차 수집 완료, 새 회차 대기 중")
                        log.info(f"  {CHAPTER_DELAY_SEC}초 대기...")
                        time.sleep(CHAPTER_DELAY_SEC)
                        continue
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