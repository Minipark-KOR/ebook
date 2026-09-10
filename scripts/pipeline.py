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
import threading
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
    """bookto31 계열 수집기: FlareSolverr + GNUBOARD5 본문 파싱.

    bookto31/newto31 등 gnuboard 소스 공용. fetch 대상 도메인은
    queue item의 source(→ sources.json base_url)를 따른다.
    """
    from services.bookto31 import fetch_chapter, parse_chapter_body
    source = item.get('source', 'bookto31')
    html = fetch_chapter(wr_id, source=source)
    if not html:
        return False, "", "fetch 실패", None
    body = parse_chapter_body(html)
    if not body or len(body) < 100:
        return False, body, f"본문 부족 ({len(body)} chars)", None
    chapter_num = _extract_chapter_from_html(html)
    return True, body, "", chapter_num


def _collect_newtoki(wr_id: int, item: dict) -> tuple[bool, str, str, Optional[int]]:
    """newtoki 수집기: Playwright + 프록시(MaskProxy 우선) + AES-GCM 복호화.

    fetch_chapter_content_full는 내부에서 브라우저를 재사용하므로
    여기서 이벤트 루프를 직접 관리하지 않는다.
    """
    from lib.toki31_playwright import fetch_chapter_content_full
    novel_id = item.get('novel_ref', '')
    if not novel_id:
        return False, "", "novel_ref 필요 (newtoki는 novel_id+episode_id 필요)", None
    try:
        result = fetch_chapter_content_full(novel_id, wr_id)
        if not result:
            return False, "", "newtoki fetch 실패 (결과 없음)", None
        return True, result, "", None
    except Exception as e:
        return False, "", f"newtoki fetch 실패: {type(e).__name__}: {e}", None


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

def _update_novel_status_from_discover(meta: dict, added: int, novel_title: str) -> None:
    """discover 결과로 연재 상태를 갱신 (소스 기반 판단 — namu 의존 없음).

    - 신규 회차 발견(added>0) → 연재중 (활동 증거), no_new_streak 초기화
    - 신규 0회차 → no_new_streak +1, 2연속이면 완결로 전환
      → 완결 소설은 이후 월간 체크 목록에서 자동 제외
    """
    this_month = datetime.now().strftime('%Y-%m')
    meta['last_discover'] = this_month
    if added > 0:
        meta['status'] = '연재중'
        meta['no_new_streak'] = 0
        meta['last_new_episode'] = this_month
    else:
        streak = int(meta.get('no_new_streak', 0)) + 1
        meta['no_new_streak'] = streak
        if streak >= 2 and meta.get('status') != '완결':
            meta['status'] = '완결'
            log.info(f"  ✓ {novel_title}: 2개월 연속 신규 회차 0 → 완결로 판정")


def discover_toki31(novel_id: int, novel_title: str = "", dry_run: bool = False) -> int:
    """toki31 소설의 전체 에피소드를 발견해 큐에 추가.

    novel_id: toki31 /novel/{novel_id}
    소스가 아닌 에피소드 목록 API(페이지네이션) 기반으로 (화수 → episode_id) 맵을 만든 뒤,
    저장되지 않은 에피소드를 wr_id=episode_id, source=toki31, novel_ref=novel_id로 큐잉한다.
    """
    import asyncio as _asyncio
    from lib.toki31_playwright import _load_proxy_env, TOKI31_BASE

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    def _fetch_episodes(only_title: bool = False):
        env = _load_proxy_env()
        # 프록시 우선순위: MaskProxy(저렴) → DataImpulse(백업)
        proxy_user = env.get("MASKPROXY_USER", "") or env.get("DATAIMPULSE_USER", "")
        proxy_pass = env.get("MASKPROXY_PASS", "") or env.get("DATAIMPULSE_PASS", "")
        proxy_host = env.get("MASKPROXY_HOST", "") or env.get("DATAIMPULSE_HOST", "")
        proxy_port = env.get("MASKPROXY_PORT", "") or env.get("DATAIMPULSE_PORT", "")
        if "dataimpulse" in proxy_host and "__cr." not in proxy_user:
            proxy_user = proxy_user + "__cr.kr"
        if not proxy_user or not proxy_pass:
            log.warning("toki31 discover: 프록시 자격증명 없음 (.env.local)")
            return "", {}
        proxy_url = "http://{}:{}".format(proxy_host, proxy_port)

        async def _run():
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                b = await p.chromium.launch(headless=True,
                    proxy={"server": proxy_url, "username": proxy_user, "password": proxy_pass})
                c = await b.new_context(user_agent=UA, locale="ko-KR")
                pg = await c.new_page()
                # 불필요한 리소스 차단 (이미지/폰트/미디어/CSS → 트래픽 절약)
                async def _block(route, request):
                    if request.resource_type in ("image", "font", "media", "stylesheet"):
                        await route.abort()
                    else:
                        await route.continue_()
                await pg.route("**/*", _block)
                await pg.goto("{}/novel/{}".format(TOKI31_BASE, novel_id), wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_timeout(2500)
                title = (await pg.title()).split("|")[0].strip()
                if only_title:
                    await b.close()
                    return title, {}
                eps: dict = {}
                def _collect():
                    return pg.eval_on_selector_all("li.novel-ep-row",
                        "els=>els.map(e=>({ep:parseInt(e.getAttribute('data-ep')), id:e.getAttribute('data-episode-id')}))")
                dom = await _collect()
                for e in dom:
                    eps[e["ep"]] = e["id"]
                guard = 0
                while guard < 40:
                    btn = pg.locator("button:has-text('이전 회차 더 보기')")
                    if await btn.count() == 0:
                        break
                    try:
                        await btn.click(timeout=8000)
                    except Exception:
                        break
                    await pg.wait_for_timeout(2000)
                    dom = await _collect()
                    before = len(eps)
                    for e in dom:
                        eps[e["ep"]] = e["id"]
                    if len(eps) == before:
                        break
                    guard += 1
                await b.close()
                return title, eps

        return _asyncio.run(_run())

    if dry_run:
        try:
            title, _ = _fetch_episodes(only_title=True)
        except Exception:
            title = novel_title
        print("TITLE:{}".format(title or novel_title or "소설 {}".format(novel_id)))
        return 0

    title, eps = _fetch_episodes()
    if not eps:
        log.warning(f"toki31 discover: {novel_id} 에피소드 없음")
        return 0

    # 큐에 추가 (저장된 화수 제외)
    queue = _load_queue()
    existing_ids = {item['wr_id'] for item in queue}
    novel_id_dir = (title or novel_title).replace(' ', '_').replace('/', '_') if (title or novel_title) else f"novel_{novel_id}"
    from lib.paths import resolve_novel_dir
    novel_dir = resolve_novel_dir(novel_id_dir)
    saved = set()
    if novel_dir.exists():
        for f in novel_dir.glob("*.json"):
            if f.name in ("meta.json", "_chapters_index.json"):
                continue
            try:
                j = json.load(open(f, encoding='utf-8'))
                if isinstance(j.get('chapter'), int):
                    saved.add(j['chapter'])
            except Exception:
                pass

    added = 0
    for ep, epid in eps.items():
        e = int(ep)
        if e in saved:
            continue
        if int(epid) in existing_ids:
            continue
        queue.append({
            "wr_id": int(epid),
            "episode_id": int(epid),
            "novel_title": title or novel_title,
            "chapter": e,
            "source": "toki31",
            "novel_ref": str(novel_id),
            "priority": 1 if e >= 800 else 5,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
            "last_error": None,
        })
        existing_ids.add(int(epid))
        added += 1
    _save_queue(queue)

    # meta 기록
    try:
        novel_dir.mkdir(parents=True, exist_ok=True)
        meta_file = novel_dir / 'meta.json'
        meta = {}
        if meta_file.exists():
            try:
                meta = json.load(open(meta_file, encoding='utf-8'))
            except Exception:
                meta = {}
        meta['main_wr_id'] = novel_id
        meta['source'] = 'toki31'
        meta['title'] = title or novel_title
        _update_novel_status_from_discover(meta, added, title or novel_title)
        with open(meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"meta 기록 실패: {e}")

    log.info(f"toki31 discover 완료: {added}개 추가 (총 {len(eps)}화)")
    return added


def _clean_page_title(title: str) -> str:
    """<title>에서 사이트명/회차 번호 접미 제거 → 순수 작품 제목.

    예:
      "문종이 화폐를 거부함 - 217화"  → "문종이 화폐를 거부함"
      "하남자의 탑 공략법 | 북토끼"   → "하남자의 탑 공략법"
    """
    import re as _re
    t = (title or "").strip()
    # 사이트명 접미 제거 (다양한 토끼 도메인 + 한글 표기)
    t = _re.sub(
        r"\s*[-–|]\s*(?:북토끼|뉴토끼|bookto31|bookto21|newto31|newtoki).*",
        "", t, flags=_re.IGNORECASE,
    )
    # 회차 번호 접미 제거 (" - 217화/편/장")
    t = _re.sub(r"\s*[-–|]\s*\d+\s*(?:화|편|장)\s*$", "", t)
    return t.strip()


def run_discover(wr_id: int, novel_title: str = "", max_pages: int = 50, source: str = "bookto31", dry_run: bool = False) -> int:
    """소스별 discover 분기.

    - gnuboard(bookto31 계열): 작품 메인에서 wr_id 발견 → 큐에 추가
    - toki31_episodes: 에피소드 목록 기반
    dry_run: 첫 페이지만 fetch해서 제목 추출 후 출력하고 종료.
    """
    from lib.sources import get_discover
    if get_discover(source) == "toki31_episodes":
        return discover_toki31(wr_id, novel_title, dry_run=dry_run)

    from services.bookto31 import extract_chapter_wr_ids_from_index
    from lib.flaresolverr_client import FlareSolverrSession
    from lib.sources import get_base_url

    fs = FlareSolverrSession(rate_limit=False)
    base = get_base_url(source)
    all_chapters = []
    seen = set()
    title = ""

    # dry_run: 첫 페이지만 fetch해서 제목 추출
    if dry_run:
        url = f"{base}/bbs/board.php?bo_table=novel&wr_id={wr_id}&epage=1"
        html = fs.fetch(url)
        if html:
            import re as _re
            title_m = _re.search(r"<title>(.*?)</title>", html)
            if title_m:
                title = _clean_page_title(title_m.group(1))
            if not title:
                og_m = _re.search(r'<meta property="og:title" content="([^"]+)"', html)
                if og_m:
                    title = _clean_page_title(og_m.group(1))
        print(f"TITLE:{title or novel_title or f'소설 {wr_id}'}")
        return 0

    # epage 파라미터로 페이지네이션 (select 드롭다운 회차 목록)
    # 화산귀환 등 일부 작품은 spage로 페이징되므로 둘 다 시도
    # 전체 회차를 확인하기 위해 max_pages 상한을 크게 잡고, 회차가 없으면 자동 중단
    max_pages = max(max_pages, 200)
    for page_param in ("epage", "spage"):
        page_seen = set()
        no_new_count = 0
        for page in range(1, max_pages + 1):
            url = f"{base}/bbs/board.php?bo_table=novel&wr_id={wr_id}&{page_param}={page}"
            html = fs.fetch(url)

            # 첫 페이지에서 제목 추출
            if page == 1 and html and not title:
                import re as _re
                title_m = _re.search(r"<title>(.*?)</title>", html)
                if title_m:
                    title = _clean_page_title(title_m.group(1))
                if not title:
                    og_m = _re.search(r'<meta property="og:title" content="([^"]+)"', html)
                    if og_m:
                        title = _clean_page_title(og_m.group(1))
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
            # 신규 0이 2연속이면 (윈도우 반복/끝) 중단 — 1회성 겹침으로 조기 중단되지 않게
            if new == 0:
                no_new_count += 1
            else:
                no_new_count = 0
            if no_new_count >= 2 and page > 1:
                break
            # 같은 페이지가 반복되면 (epage를 무시하는 작품) 다음 파라미터로
            if new == 0 and page >= 1 and page_seen and all(c in page_seen for c, _ in page_chapters):
                log.info(f"  {page_param}={page}: 중복 페이지, 중단")
                break

    # 큐에 추가 (queue에 이미 있거나, 파일로 이미 저장된 회차는 제외)
    queue = _load_queue()
    existing_ids = {item['wr_id'] for item in queue}

    # 전달된 wr_id로 아무 회차도 발견 못 했으면 (잘못된 main_wr_id 케이스),
    # 저장된 챕터에서 유효한 wr_id를 뽑아 재시도. (에피소드 셀렉트가 0개 나옴)
    if not all_chapters and not dry_run:
        novel_id_dir = novel_title.replace(' ', '_').replace('/', '_') if novel_title else f"novel_{wr_id}"
        from lib.paths import resolve_novel_dir
        novel_dir = resolve_novel_dir(novel_id_dir)
        saved_wr = None
        if novel_dir.exists():
            for f in sorted(novel_dir.glob("*.json"), key=lambda p: int(p.stem)):
                if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
                    continue
                saved_wr = int(f.stem)
                break
        if saved_wr and saved_wr != wr_id:
            log.warning(f"  wr_id={wr_id}로 회차 발견 실패 → 저장된 챕터 wr_id={saved_wr}로 재시도")
            for page_param in ("epage", "spage"):
                for page in range(1, min(max_pages, 200) + 1):
                    url = f"{base}/bbs/board.php?bo_table=novel&wr_id={saved_wr}&{page_param}={page}"
                    html = fs.fetch(url)
                    if not html or len(html) < 1000:
                        break
                    page_chapters = extract_chapter_wr_ids_from_index(html)
                    if not page_chapters:
                        break
                    for ch_wr_id, chapter in page_chapters:
                        if ch_wr_id not in seen and ch_wr_id != saved_wr:
                            seen.add(ch_wr_id)
                            all_chapters.append((ch_wr_id, chapter))
                    if not page_chapters or all(c[0] in seen for c in page_chapters):
                        break

    # 이미 저장된 회차 (동일 작품 디렉토리의 wr_id.json)
    saved_ids = set()
    try:
        novel_id_dir = novel_title.replace(' ', '_').replace('/', '_') if novel_title else f"novel_{wr_id}"
        from lib.paths import resolve_novel_dir
        novel_dir = resolve_novel_dir(novel_id_dir)
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
    # 소스 무관 저장된 chapter (index 캐시 기반) — wr_id와 무관하게 재다운로드 방지
    saved_chapters = _load_saved_chapters(novel_title) if novel_title else set()
    for ch_wr_id, chapter in all_chapters:
        if ch_wr_id in existing_ids or ch_wr_id in saved_ids:
            continue
        # chapter 기준 dedup — 같은 chapter가 다른 wr_id로 저장돼 있어도 스킵
        # (discover와 collect가 동시 진행되며 저장되는 경합 상황 대응)
        if chapter is not None and chapter in saved_chapters:
            log.info(f"  ↷ chapter {chapter} 이미 저장됨 — 큐 추가 스킵 (wr_id={ch_wr_id})")
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
            from lib.paths import resolve_novel_dir
            novel_dir = resolve_novel_dir(novel_id_dir)
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
            # 소스 기반 연재 상태 갱신 (완결 판정 포함)
            _update_novel_status_from_discover(meta, added, novel_title)
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


# 소설별 저장된 chapter 번호 캐시 (소스 무관 — 재다운로드 방지)
# key: novel_id(공백→_), value: {chapter 번호}
_saved_chapters_cache: dict[str, set] = {}


def _load_saved_chapters(novel_title: str) -> set:
    """소설 디렉토리에 이미 저장된 chapter 번호 집합 (source 무관).

    어떤 소스(bookto31/toki31)로든 저장됐으면 포함한다. 소스 간 중복
    (같은 chapter를 서로 다른 wr_id로 재발견)을 막기 위한 소스 무관 dedup.

    _chapters_index.json 캐시를 우선 사용 (save_chapter가 자동 갱신),
    없으면 전체 JSON 스캔으로 폴백.
    """
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    if novel_id in _saved_chapters_cache:
        return _saved_chapters_cache[novel_id]

    from lib.paths import resolve_novel_dir
    novel_dir = resolve_novel_dir(novel_id)
    saved: set = set()

    # 1) 인덱스 캐시 우선 (빠름 — 파일별 스캔 회피)
    #    인덱스 chapter 수와 실제 챕터 파일 수가 같을 때만 신뢰 (부분/오래된 인덱스 방지)
    try:
        from services.data import load_chapters_index
        idx = load_chapters_index(novel_dir) or []
        file_count = 0
        if novel_dir.exists():
            file_count = sum(
                1 for f in novel_dir.glob("*.json")
                if f.name not in ("meta.json", "_chapters_index.json")
            )
        if idx and len(idx) == file_count:
            for c in idx:
                ch = c.get('chapter')
                if isinstance(ch, int):
                    saved.add(ch)
            _saved_chapters_cache[novel_id] = saved
            return saved
    except Exception:
        pass

    # 2) 폴백: 전체 JSON 스캔
    if novel_dir.exists():
        for f in novel_dir.glob("*.json"):
            if f.name in ("meta.json", "_chapters_index.json"):
                continue
            try:
                j = json.load(open(f, encoding='utf-8'))
                ch = j.get('chapter')
                if isinstance(ch, int):
                    saved.add(ch)
            except Exception:
                pass
    _saved_chapters_cache[novel_id] = saved
    return saved


def _invalidate_saved_chapters(novel_title: str) -> None:
    """저장 후 캐시 무효화 — 다음 collect에서 재스캔하도록."""
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    _saved_chapters_cache.pop(novel_id, None)


def _run_collect_locked(limit: int = 0, source_filter: str = "") -> dict:
    """run_collect 본체 (락 보유 상태에서 실행).

    트래픽 가드: 유료 프록시 소스(traffic_limited=True, 예: toki31)의
    일일 한도 초과 시 해당 소스만 중단. bookto31(FlareSolverr 로컬)은
    트래픽 가드와 무관하게 계속 처리된다.
    (loop는 소스별로 순회하므로 한 소스의 중단이 다른 소스를 막지 않음)
    """
    from lib.sources import get_traffic_limited
    from lib.traffic_guard import reset_if_new_day, is_exceeded, add_bytes, summary
    from lib.toki31_playwright import get_traffic_total_bytes

    # 트래픽 가드 적용 여부 — 특정 소스 필터 시 그 소스가 유료 프록시인지에 따라.
    # source_filter가 없으면(전체) 유료 소스 포함 가능 → 가드 적용.
    traffic_limited = (not source_filter) or get_traffic_limited(source_filter)

    reset_if_new_day()
    if traffic_limited and is_exceeded():
        s = summary()
        log.warning(
            f"  ⏸ 일일 트래픽 한도 초과 ({s['used_mb']}MB/{s['daily_limit_mb']}MB) "
            f"— 자정까지 {source_filter or '유료 소스'} 중단 (queue {len(_load_queue())}건 보존)"
        )
        return {"processed": 0, "errors": [], "remaining": len(_load_queue()), "traffic_exceeded": True}

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
    dedup_skipped = 0
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

        # 재다운로드 방지: 소스 무관 이미 저장된 chapter면 다운로드 없이 스킵
        # (bookto31/toki31이 같은 chapter를 서로 다른 wr_id로 재발견하는 경우 방지)
        chapter_num = item.get('chapter')
        if chapter_num is not None and novel_title:
            if chapter_num in _load_saved_chapters(novel_title):
                log.info(
                    f"  ↷ chapter {chapter_num} 이미 저장됨 — 다운로드 스킵 (wr_id={wr_id})"
                )
                removed_ids.add(wr_id)
                dedup_skipped += 1
                continue

        # collector 선택 (source별 분기 — collector 키는 sources.json에서 해석)
        from lib.sources import get_collector
        collector = COLLECTORS.get(get_collector(source))
        if not collector:
            log.warning(f"  ✗ 알 수 없는 source: {source}")
            errors.append({"wr_id": wr_id, "error": f"Unknown source: {source}"})
            removed_ids.add(wr_id)
            continue

        # 3회 재시도 (fetch 시간 측정 → 적응형 딜레이)
        # 트래픽 실측: collect 전후 프록시 누적 바이트 delta를 일일 한도에 반영
        traffic_before = get_traffic_total_bytes()
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
        traffic_delta = get_traffic_total_bytes() - traffic_before
        if traffic_delta > 0:
            add_bytes(traffic_delta, chapter=True)

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
        _invalidate_saved_chapters(novel_title)  # 캐시 갱신 — 이후 중복 스킵 정확성
        _body_len = body[1] if isinstance(body, tuple) and len(body) == 2 else body
        log.info(f"  ✓ wr_id={wr_id} 저장 완료 ({len(_body_len)} chars)")

        # 큐에서 제거 (전체 queue 기준)
        removed_ids.add(wr_id)
        processed += 1
        if novel_title:
            touched_novels[novel_title.replace(' ', '_').replace('/', '_')] = novel_title

        # 다음 챕터 전 대기 — 소스별 적응형 딜레이 (업계 표준: 10 × fetch 시간)
        # 서버 응답시간 × 10 을 소스별 [delay_min, delay_max] 구간에 클램프:
        #   서버가 빠르면(fetch 짧음) 딜레이 축소, 느리면 확대 (동적 politeness)
        #   bookto31: 30~300s / toki31: 5~30s (sources.json delay_min/max)
        if len(queue) > 0:
            from lib.sources import get_delay_bounds
            d_min, d_max = get_delay_bounds(source)
            delay = max(float(d_min), min(float(d_max), fetch_elapsed * 10))
            log.info(
                f"  {delay:.0f}초 대기 (fetch {fetch_elapsed:.1f}s × 10, "
                f"range {d_min}~{d_max}s, source={source})..."
            )
            time.sleep(delay)

        # 일일 한도 도달 시 남은 회차는 다음 날 재개 (현재 회차는 위에서 처리/저장 완료)
        # 유료 프록시 소스에만 적용 (bookto31 등 무료 소스는 계속)
        if traffic_limited and is_exceeded():
            s = summary()
            log.warning(
                f"  ⏸ 일일 트래픽 한도 도달 ({s['used_mb']}MB/{s['daily_limit_mb']}MB) "
                f"— 남은 {len(queue) - i - 1}건은 자정 이후 재개"
            )
            break

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
        "dedup_skipped": dedup_skipped,
        "last_result": {
            "processed": processed,
            "errors": len(errors),
            "remaining": len(remaining_queue),
            "dedup_skipped": dedup_skipped,
        },
    })
    return {"processed": processed, "errors": errors, "remaining": len(remaining_queue), "dedup_skipped": dedup_skipped}


# === 3단계: ENRICH — namu.wiki 메타데이터 보강 ===

# namu.wiki는 30분 rate limit이 있어 호출 시 최대 30분 대기할 수 있다.
# 수집 루프를 블록하지 않도록 백그라운드 실행 + 직렬화(동시 namu 호출 방지).
_ENRICH_LOCK = threading.Lock()


def run_enrich_background(novel_id: str) -> None:
    """메타데이터 갱신을 백그라운드 스레드로 실행 (수집 루프 비블록)."""
    def _job():
        try:
            with _ENRICH_LOCK:
                run_enrich(novel_id, force=True)
        except Exception as e:
            log.warning(f"  백그라운드 메타 갱신 실패 ({novel_id}): {type(e).__name__}: {e}")
    threading.Thread(target=_job, daemon=True).start()

def run_enrich(novel_id: Optional[str] = None, force: bool = False) -> dict:
    """meta.json에 namu.wiki 메타데이터 보강.

    force=True면 namu_attempted/작가 유무와 무관하게 항상 갱신
    (URL 수신 시, 월간 discover에서 신규 회차 발견 시 호출).
    참고: status는 namu가 아닌 discover(소스 기반)가 결정하므로 여기서 덮어쓰지 않는다.
    """
    from services.metadata_namu import get_metadata
    from lib.paths import find_novel_dir, iter_novel_dirs

    results = {"enriched": 0, "skipped": 0, "errors": 0}

    if novel_id:
        d = find_novel_dir(novel_id)
        targets = [d] if d else []
    else:
        targets = [p for _mt, p in iter_novel_dirs()]
    for novel_dir in targets:
        if not novel_dir.is_dir():
            continue
        meta_file = novel_dir / 'meta.json'
        if not meta_file.exists():
            continue

        with open(meta_file) as f:
            meta = json.load(f)

        if not force and (meta.get('namu_attempted') or meta.get('author', '') != '미상'):
            results['skipped'] += 1
            continue

        # namu_attempted 플래그 설정
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
    from services.data import rebuild_chapters_index
    from lib.paths import find_novel_dir, iter_novel_dirs

    results = {"indexed": 0, "errors": 0}
    if novel_id:
        d = find_novel_dir(novel_id)
        targets = [d] if d else []
    else:
        targets = [p for _mt, p in iter_novel_dirs()]

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

    novel_id = novel_title.replace(' ', '_').replace('/', '_')

    # 3. index
    log.info("\n[3/5] INDEX — 인덱스 캐시 재구축")
    indexed = run_index(novel_id)
    results['index'] = indexed

    # 4. 나머지 collect — 수집을 먼저 끝낸다 (namu 30분 대기로 수집이 늦어지지 않게)
    log.info("\n[4/5] COLLECT — 나머지 수집")
    rest = run_collect(limit=0, source_filter=source)
    results['collect_rest'] = rest

    # 5. enrich — URL 수신 시이므로 항상 갱신 (force). namu rate limit(최대 30분)으로
    #    수집을 블록하지 않도록 백그라운드로 실행.
    log.info("\n[5/5] ENRICH — 메타데이터 보강 (백그라운드)")
    run_enrich_background(novel_id)

    # revalidate (마지막)
    log.info("\n[5/5] REVALIDATE — 캐시 갱신")
    revalidated = run_revalidate(novel_id)
    results['revalidate'] = revalidated

    log.info("\n" + "=" * 50)
    log.info("파이프라인 완료")
    log.info("=" * 50)
    return results


def _auto_discover() -> None:
    """저장된 연재작들의 새 회차를 주기적으로 discover.

    각 소설의 meta.source(등록된 소스)를 읽어 해당 소스의 discover로 새 회차를 찾는다.
    queue에 없거나 이미 완결인 작품은 스킵. (완결 → 월간 체크 목록에서 제외)
    """
    from lib.sources import get_discover
    from lib.paths import iter_novel_dirs

    for _media_type, novel_dir in iter_novel_dirs():
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
        # 완결작은 새 회차 없음 (월간 체크 목록에서 제외)
        if meta.get('status') == '완결':
            continue
        source = meta.get('source') or 'bookto31'
        # 현재 gnuboard(bookto31 계열)만 자동 discover 지원. toki31 등은 에피소드 큐가
        # 이미 있으므로 스킵 (추후 toki31 discover 모듈 추가 시 활성화).
        if get_discover(source) != "gnuboard":
            continue
        main_wr_id = meta.get('main_wr_id')
        title = meta.get('title') or novel_dir.name.replace('_', ' ')
        # main_wr_id가 없거나 잘못됐을 수 있으므로, 기존 챕터에서 유효한 wr_id를 유도.
        # (main_wr_id가 틀리면 discover가 에피소드 셀렉트를 못 읽고 빈 결과로 끝남 — 재발 방지)
        if not main_wr_id:
            for f in novel_dir.glob("*.json"):
                if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
                    continue
                main_wr_id = int(f.stem)
                log.info(f"  {title}: main_wr_id 없음 → 저장된 챕터에서 유도 ({main_wr_id})")
                break
        if not main_wr_id:
            continue
        log.info(f"  auto-discover: {title} (main_wr_id={main_wr_id}, source={source})")
        try:
            added = run_discover(int(main_wr_id), title, max_pages=200, source=source)
            # 신규 회차 발견(queue 추가) 시 그 소설의 메타데이터도 갱신
            # (namu 30분 rate limit 때문에 수집 루프를 막지 않도록 백그라운드)
            if added > 0:
                log.info(f"  {title}: 신규 {added}화 발견 → 메타데이터 갱신(백그라운드)")
                run_enrich_background(novel_dir.name)
        except Exception as e:
            log.warning(f"  {title} discover 실패: {e}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]

    if cmd == "traffic":
        """일일 트래픽 사용량/한도 상태 출력."""
        from lib.traffic_guard import summary, reset_if_new_day, seconds_until_next_day
        reset_if_new_day()
        s = summary()
        print(f"일일 한도:   {s['daily_limit_mb']} MB")
        print(f"사용량:      {s['used_mb']} MB ({s['chapters']}회차)")
        print(f"잔여:        {s['remaining_mb']} MB")
        print(f"한도 초과:   {'예' if s['exceeded'] else '아니오'}")
        if s['exceeded']:
            print(f"자정 재개까지: {seconds_until_next_day()}초")
        # dedup 스킵 통계 (재다운로드 방지 실적)
        try:
            st = json.load(open(STATUS_FILE, encoding='utf-8'))
            lr = st.get('last_result') or {}
            if lr.get('dedup_skipped') is not None:
                print(f"마지막 collect dedup 스킵: {lr['dedup_skipped']}건")
        except Exception:
            pass
        return 0

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
        from lib.paths import iter_novel_dirs
        from services.epub import maybe_build_epub
        targets = sys.argv[2:]
        built = 0
        for _media_type, novel_dir in iter_novel_dirs():
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

        bookto31/toki31: 연재작 특성상 queue가 비어도 종료하지 않고 대기한다.
        (URL/discover로 회차가 추가되면 계속 수집)
        """
        # novel_title은 --source 같은 플래그가 아닌 위치 인자만
        novel_title = None
        positional = [a for a in sys.argv[2:] if not a.startswith("--")]
        if positional:
            novel_title = positional[0]
        source = _parse_source()
        log.info("=" * 50)
        log.info(f"파이프라인 루프 시작 (source={source}, novel={novel_title or '전체'}, Ctrl+C로 중단)")
        log.info("=" * 50)
        # PID 기록
        PID_FILE.write_text(str(os.getpid()))
        cycle = 0
        last_discover_day = None  # 이번 달에 discover 실행했는지 추적
        # 다중 소스: 사이클 대기는 짧게, 소스별 페이싱은 run_collect 내부 딜레이가 담당
        cycle_delay = 1
        try:
            while True:
                cycle += 1
                log.info(f"\n--- Cycle {cycle} ---")
                _write_status({"phase": "loop", "cycle": cycle, "source": source or "all"})
                # systemd watchdog 신호 (WatchdogSec 대응)
                _sd_notify(f"cycle {cycle}")

                # 매월 1일 1회 연재작 새 회차 감지 (등록된 모든 소스)
                today = datetime.now().strftime("%Y-%m")
                if today != last_discover_day:
                    if datetime.now().day == 1:
                        last_discover_day = today
                        log.info("discover: 매월 1일 연재작 새 회차 확인")
                        try:
                            _auto_discover()
                        except Exception as e:
                            log.warning(f"auto-discover 실패: {e}")
                    else:
                        pass

                # collect — 소스별 1개씩 처리 (처리 격리)
                # toki31(유료 프록시)이 일일 한도/실패로 중단돼도 bookto31(무료)은 계속.
                from lib.sources import list_sources
                sources = list_sources()
                cycle_processed = 0
                cycle_remaining = 0
                traffic_exceeded_any = False
                for src in sources:
                    result = run_collect(limit=1, source_filter=src)
                    cycle_processed += result.get('processed', 0)
                    cycle_remaining += result.get('remaining', 0)
                    if result.get('traffic_exceeded'):
                        traffic_exceeded_any = True
                        log.warning(f"  [{src}] 일일 트래픽 한도 도달 — {src}만 자정까지 대기")

                # 유료 소스가 전부 한도 도달이면 자정까지 대기 (무료 소스는 위에서 이미 처리)
                if traffic_exceeded_any and cycle_processed == 0:
                    from lib.traffic_guard import seconds_until_next_day
                    wait = seconds_until_next_day()
                    log.info(f"  ⏸ 유료 소스 한도 도달 — {wait}초(자정) 후 재개")
                    time.sleep(min(wait, 300))
                    continue

                if cycle_processed == 0 and cycle_remaining == 0:
                    # 연재작: queue가 비어도 계속 대기 (새 회차 추가 대기)
                    log.info("큐 비어 있음 - 새 회차 대기 중")
                    log.info(f"  {cycle_delay}초 대기...")
                    time.sleep(cycle_delay)
                    continue

                # index (해당 소설만)
                if novel_title:
                    run_index(novel_title)

                # revalidate (해당 소설만)
                if novel_title:
                    run_revalidate(novel_title)

                log.info(f"--- Cycle {cycle} 완료 (처리: {cycle_processed}, 남음: {cycle_remaining}) ---")

                # 큐가 비었으면 계속 대기 (연재작)
                remaining = _load_queue()
                if not remaining:
                    log.info("모든 회차 수집 완료, 새 회차 대기 중")
                    log.info(f"  {cycle_delay}초 대기...")
                    time.sleep(cycle_delay)
                    continue

                log.info(f"  {cycle_delay}초 대기...")
                time.sleep(cycle_delay)
        except KeyboardInterrupt:
            log.info("루프 중단 (사용자 요청)")

    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())