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
    """진행 상황을 status.json에 기록 (loop/collect가 주기적으로 호출).

    domain_health(도메인 헬스) 등 모니터링 필드는 새 데이터에 없으면
    기존 값을 보존한다 — collect 상태 기록이 헬스 결과를 덮어쓰지 않도록.
    """
    try:
        data = dict(data)
        if "domain_health" not in data:
            try:
                if STATUS_FILE.exists():
                    prev = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
                    if isinstance(prev, dict) and prev.get("domain_health"):
                        data["domain_health"] = prev["domain_health"]
            except Exception:
                pass
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
# 도메인 헬스체크 — 사이트 주소 변경 자동 감지/전환
# ============================================================

DOMAIN_CHECK_INTERVAL_SEC = 1800  # 30분 간격
_last_domain_check = [0.0]


def _probe_bookto31(base: str) -> Optional[bool]:
    """bookto31 헬스체크용: 홈 페이지 실제 응답으로 생존 확인.

    None이면 판별 불가 (FlareSolverr 인프라 다운).
    """
    from services.bookto31 import probe_base
    return probe_base(base)


def _check_domain_health_once() -> None:
    """소스별 도메인 생존 확인. 사망 시 후보 도메인으로 자동 전환.

    - bookto31: FlareSolverr 홈 페이지 실제 응답으로 확인 (rate limit 미적용)
    - toki31: DNS 해석 여부만 확인 (유료 프록시 트래픽 절약)
    결과는 status.json의 domain_health에 기록한다.
    """
    from lib.sources import list_sources
    from lib.domain_router import check_domain_health

    now = time.time()
    if now - _last_domain_check[0] < DOMAIN_CHECK_INTERVAL_SEC:
        return
    _last_domain_check[0] = now

    health = {}
    for src in list_sources():
        try:
            probe_fn = _probe_bookto31 if src == "bookto31" else None
            result = check_domain_health(src, probe_fn=probe_fn)
            health[src] = {"status": result["status"], "base_url": result.get("base_url")}
            if result["status"] != "ok":
                log.warning(
                    f"도메인 헬스 [{src}] {result['status']} "
                    f"base_url={result.get('base_url')}"
                )
        except Exception as e:
            log.warning(f"도메인 헬스체크 실패 [{src}]: {e}")
    if health:
        # 기존 status.json의 phase/cycle을 유지하며 domain_health만 병합
        try:
            merged = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {}
            if not isinstance(merged, dict):
                merged = {}
        except Exception:
            merged = {}
        merged["domain_health"] = health
        _write_status(merged)


# ============================================================
# 수집기 레지스트리 — source별 collector 분기
# ============================================================

def _collect_bookto31(wr_id: int, item: dict) -> tuple[bool, str, str, Optional[int]]:
    """bookto31 계열 수집기: FlareSolverr + GNUBOARD5 본문 파싱.

    bookto31/newto31 등 gnuboard 소스 공용. fetch 대상 도메인은
    queue item의 source(→ sources.json base_url), 게시판은 item의 bo_table을 따른다.
    """
    from services.bookto31 import fetch_chapter, parse_chapter_body
    source = item.get('source', 'bookto31')
    bo_table = item.get('bo_table', 'novel')
    html = fetch_chapter(wr_id, source=source, bo_table=bo_table)
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


def _download_webtoon_images(wr_id: int, urls: list) -> list:
    """웹툰 챕터 이미지를 로컬로 다운로드 → 로컬/원본 URL 목록 반환.

    저장: /opt/ai_data/flaresolverr/webtoon_images/{wr_id}/{NNNN}.{ext}
    다운로드 실패/차단 응답 이미지는 원본 URL을 유지해 회차가 비지 않게 한다.
    """
    import urllib.request

    base = Path('/opt/ai_data/flaresolverr/webtoon_images') / str(wr_id)
    base.mkdir(parents=True, exist_ok=True)
    out: list = []
    for i, url in enumerate(urls, 1):
        ext = Path(url.split('?')[0]).suffix.lower()
        if ext not in ('.jpg', '.jpeg', '.webp', '.png'):
            ext = '.jpg'
        target = base / f"{i:04d}{ext}"
        if target.exists():
            out.append(f"/api/webtoon_images/{wr_id}/{target.name}")
            continue
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            r = urllib.request.urlopen(req, timeout=20)
            data = r.read()
            if len(data) < 1000:  # 차단/에러 페이지 응답 → 원본 유지
                out.append(url)
                continue
            target.write_bytes(data)
            out.append(f"/api/webtoon_images/{wr_id}/{target.name}")
        except Exception:
            out.append(url)
    return out


def retry_failed_webtoon_images() -> dict:
    """웹툰 챕터에 남은 원격 이미지 URL을 재다운로드 (월 1회 체크).

    CDN이 일시적으로 404/차단했다가 복구된 이미지를 로컬로 채운다.
    content에서 https 원격 URL을 찾아 다운로드 성공 시 로컬 경로로 교체한다.
    """
    import re as _re
    import urllib.request

    base = Path('/opt/ai_data/flaresolverr/webtoons')
    images_base = Path('/opt/ai_data/flaresolverr/webtoon_images')
    if not base.exists():
        return {"checked": 0, "fixed": 0}
    fixed = 0
    checked = 0
    for nd in sorted(base.iterdir()):
        if not nd.is_dir() or nd.name.startswith('.'):
            continue
        for f in sorted(nd.glob('*.json')):
            if f.name in ('meta.json', '_chapters_index.json') or not f.stem.isdigit():
                continue
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            content = d.get('content', '') or ''
            if 'https://' not in content:
                continue
            wr_id = int(f.stem)
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                m = _re.match(r'^!\[(\d+)\]\((https?://[^\s\)]+)\)$', line.strip())
                if not m:
                    new_lines.append(line)
                    continue
                idx = int(m.group(1))
                url = m.group(2)
                ext = Path(url.split('?')[0]).suffix.lower()
                if ext not in ('.jpg', '.jpeg', '.webp', '.png'):
                    ext = '.jpg'
                target = images_base / str(wr_id) / f"{idx:04d}{ext}"
                if target.exists():
                    new_lines.append(f"![{idx}](/api/webtoon_images/{wr_id}/{target.name})")
                    continue
                checked += 1
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    r = urllib.request.urlopen(req, timeout=25)
                    data = r.read()
                    if len(data) >= 1000:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        new_lines.append(f"![{idx}](/api/webtoon_images/{wr_id}/{target.name})")
                        fixed += 1
                        log.info(f"  ↻ 웹툰 이미지 복구: {wr_id}/{idx:04d}")
                        continue
                except Exception:
                    pass
                new_lines.append(line)
            if new_lines != lines:
                d['content'] = '\n'.join(new_lines)
                f.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if checked:
        log.info(f"웹툰 실패 이미지 재시도: {checked}개 확인, {fixed}개 복구")
    return {"checked": checked, "fixed": fixed}


def _collect_webtoon(wr_id: int, item: dict) -> tuple[bool, str, str, Optional[int]]:
    """웹툰 회차 수집: 챕터 이미지를 로컬 다운로드 후 markdown 본문으로 저장.

    본문이 텍스트가 아닌 이미지(웹툰/만화)인 회차용. 이미지는
    webtoon_images/{wr_id}/ 에 저장하고 content는 로컬 경로
    "![n](/api/webtoon_images/{wr_id}/NNNN.ext)" 행으로 기록한다.
    source/bo_table은 queue item에서 사용.
    """
    from services.bookto31 import fetch_chapter, extract_webtoon_images
    source = item.get('source', 'bookto31')
    bo_table = item.get('bo_table', 'novel')
    html = fetch_chapter(wr_id, source=source, bo_table=bo_table)
    if not html:
        return False, "", "fetch 실패", None
    imgs = extract_webtoon_images(html)
    if not imgs:
        return False, "", f"콘텐츠 이미지 없음 ({len(html)} bytes)", None
    local_imgs = _download_webtoon_images(wr_id, imgs)
    body = "\n".join(f"![{i+1}]({u})" for i, u in enumerate(local_imgs))
    chapter_num = _extract_chapter_from_html(html)
    return True, body, "", chapter_num


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


def _parse_bo_table() -> str:
    """CLI 인자에서 --bo-table 추출 (gnuboard 게시판). 없으면 기본 "novel"."""
    for i, arg in enumerate(sys.argv):
        if arg == "--bo-table" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return "novel"

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
    from lib.toki31_playwright import _load_proxy_env, _toki_base
    from lib.domain_router import auto_update_base

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    def _fetch_episodes(only_title: bool = False):
        env = _load_proxy_env()
        # 프록시 우선순위: DataImpulse(한국 IP 지원) → MaskProxy(백업)
        proxy_user = env.get("DATAIMPULSE_USER", "") or env.get("MASKPROXY_USER", "")
        proxy_pass = env.get("DATAIMPULSE_PASS", "") or env.get("MASKPROXY_PASS", "")
        proxy_host = env.get("DATAIMPULSE_HOST", "") or env.get("MASKPROXY_HOST", "")
        proxy_port = env.get("DATAIMPULSE_PORT", "") or env.get("MASKPROXY_PORT", "")
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
                await pg.goto("{}/novel/{}".format(_toki_base(), novel_id), wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_timeout(2500)
                # 리다이렉트 최종 URL 감지 → sources.json base_url 자동 갱신
                try:
                    auto_update_base("toki31", f"{_toki_base()}/novel/{novel_id}", pg.url)
                except Exception:
                    pass
                # og:title가 노벨 본제목을 직접 담고 있으므로 우선 사용
                try:
                    page_html = await pg.content()
                    import re as _re
                    og_m = _re.search(r'<meta property="og:title" content="([^"]+)"', page_html)
                    if og_m:
                        title = og_m.group(1).strip()
                except Exception:
                    pass
                if not title:
                    title = await pg.title()
                    title = title.strip()
                    if '|' in title:
                        parts = title.split('|')
                        title = parts[1].strip()
                    elif ' - ' in title:
                        parts = title.split(' - ')
                        title = parts[0].strip()
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
    added_items = []
    for ep, epid in eps.items():
        e = int(ep)
        if e in saved:
            continue
        if int(epid) in existing_ids:
            continue
        item = {
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
        }
        queue.append(item)
        added_items.append(item)
        existing_ids.add(int(epid))
        added += 1
    _merge_into_queue(add_items=added_items)

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


def _download_cover(novel_title: str, cover_url: str) -> Optional[str]:
    """작품 표지를 로컬 covers/에 다운로드 → /api/covers 로컬 URL 반환.

    웹툰 업로드 CDN(imgspeedtoki 등)은 서버에서 받을 수 있지만 og:image 호스트
    (speedwebgo 등)는 403인 경우가 많다. 다운로드 가능한 CDN 표지가 있으면
    로컬 저장을 우선해 image-proxy(502)에 의존하지 않는다.
    실패 시 None (호출부에서 og:image 직접 URL 폴백).
    """
    import urllib.request
    from urllib.parse import quote

    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    ext = Path(cover_url.split('?')[0]).suffix.lower()
    if ext not in ('.webp', '.jpg', '.jpeg', '.png'):
        ext = '.jpg'
    target = Path('/opt/ai_data/flaresolverr/covers') / f"{novel_id}{ext}"
    try:
        req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
        r = urllib.request.urlopen(req, timeout=20)
        data = r.read()
        if len(data) < 1000:  # 차단 페이지/에러 응답 방지
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"/api/covers/{quote(target.name)}"
    except Exception:
        return None


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


def run_discover(wr_id: int, novel_title: str = "", max_pages: int = 50, source: str = "bookto31", dry_run: bool = False, bo_table: str = "novel") -> int:
    """소스별 discover 분기.

    - gnuboard(bookto31 계열): 작품 메인에서 wr_id 발견 → 큐에 추가
      bo_table: gnuboard 게시판(콘텐츠 종류) — URL의 bo_table을 그대로 사용
      (예: newto31의 fafa19=웹툰, novel=소설)
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
    cover_url = ""
    from lib.sources import get_media_type
    media_type = get_media_type(source, bo_table)

    # dry_run: 첫 페이지만 fetch해서 제목 추출
    if dry_run:
        url = f"{base}/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}&epage=1"
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
            url = f"{base}/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}&{page_param}={page}"
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
            # 첫 페이지에서 표지 추출 — 업로드 CDN cover 이미지를 로컬 저장 우선,
            # 없으면 og:image(직접 URL) 폴백. (웹툰/소설 공통)
            if page == 1 and not cover_url and html:
                import re as _re
                cover_cdn = _re.search(
                    r'https?://[^"\'\s]*imgspeedtoki[^"\'\s]*/cover/[^"\'\s]+', html
                )
                local_cover = None
                if cover_cdn:
                    local_cover = _download_cover(title or novel_title or f"작품_{wr_id}", cover_cdn.group(0))
                if local_cover:
                    cover_url = local_cover
                else:
                    ogi = _re.search(r'<meta property="og:image" content="([^"]+)"', html)
                    if ogi:
                        cover_url = ogi.group(1).strip()
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
                    url = f"{base}/bbs/board.php?bo_table={bo_table}&wr_id={saved_wr}&{page_param}={page}"
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
        from lib.paths import novel_dir_for, resolve_novel_dir
        novel_dir = novel_dir_for(novel_title or f"novel_{wr_id}", media_type)
        if not novel_dir.exists():
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
    added_items = []
    # 소스 무관 저장된 chapter (index 캐시 기반) — wr_id와 무관하게 재다운로드 방지
    saved_chapters = _load_saved_chapters(novel_title, media_type) if novel_title else set()
    for ch_wr_id, chapter in all_chapters:
        if ch_wr_id in existing_ids or ch_wr_id in saved_ids:
            continue
        # chapter 기준 dedup — 같은 chapter가 다른 wr_id로 저장돼 있어도 스킵
        # (discover와 collect가 동시 진행되며 저장되는 경합 상황 대응)
        if chapter is not None and chapter in saved_chapters:
            log.info(f"  ↷ chapter {chapter} 이미 저장됨 — 큐 추가 스킵 (wr_id={ch_wr_id})")
            continue
        item = {
            "wr_id": ch_wr_id,
            "novel_title": novel_title,
            "chapter": chapter,
            "source": source,  # ← source 필드
            "bo_table": bo_table,  # ← 게시판(콘텐츠 종류)
            "priority": 1 if chapter >= 800 else 5,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
            "last_error": None,
        }
        queue.append(item)
        added_items.append(item)
        existing_ids.add(ch_wr_id)
        added += 1

    _merge_into_queue(add_items=added_items)

    # 작품 메인 wr_id 기록 (loop의 자동 discover를 위해 meta.json에 저장)
    if novel_title:
        try:
            novel_id_dir = novel_title.replace(' ', '_').replace('/', '_')
            from lib.paths import novel_dir_for, resolve_novel_dir
            novel_dir = novel_dir_for(novel_title, media_type)
            if not novel_dir.exists():
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
            meta['bo_table'] = bo_table
            meta['media_type'] = media_type
            meta['title'] = novel_title
            if cover_url:
                meta['coverUrl'] = cover_url
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


def _load_saved_chapters(novel_title: str, media_type: str = "novel") -> set:
    """소설 디렉토리에 이미 저장된 chapter 번호 집합 (source 무관).

    어떤 소스(bookto31/toki31)로든 저장됐으면 포함한다. 소스 간 중복
    (같은 chapter를 서로 다른 wr_id로 재발견)을 막기 위한 소스 무관 dedup.
    media_type: 저장 폴더 결정 ("novel"|"comic"|"webtoon").

    _chapters_index.json 캐시를 우선 사용 (save_chapter가 자동 갱신),
    없으면 전체 JSON 스캔으로 폴백.
    """
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    cache_key = f"{media_type}:{novel_id}"
    if cache_key in _saved_chapters_cache:
        return _saved_chapters_cache[cache_key]

    from lib.paths import novel_dir_for, resolve_novel_dir
    novel_dir = novel_dir_for(novel_title, media_type)
    if not novel_dir.exists():
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
            _saved_chapters_cache[cache_key] = saved
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
    _saved_chapters_cache[cache_key] = saved
    return saved


def _invalidate_saved_chapters(novel_title: str) -> None:
    """저장 후 캐시 무효화 — 다음 collect에서 재스캔하도록."""
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    for mt in ("novel", "comic", "webtoon"):
        _saved_chapters_cache.pop(f"{mt}:{novel_id}", None)
    _saved_chapters_cache.pop(novel_id, None)  # 하위 호환
    _invalidate_saved_hash(novel_title)


# ============================================================
# 중복 본문 감지/처리 — 동일 본문이 다른 화수로 저장되는 것 방지
# (소스 wr_id 매핑 오류로 같은 내용이 다른 chapter 번호로 중복 저장되는 케이스)
# ============================================================

DUPLICATES_FILE = WATCHER_DIR / 'duplicates.json'
_saved_hash_cache: dict[str, dict] = {}


def _content_hash(content: str) -> str:
    """본문 내용 해시 (MD5)."""
    import hashlib
    return hashlib.md5(content.encode('utf-8', errors='replace')).hexdigest()


def _claimed_chapter_strict(content: str) -> Optional[int]:
    """본문 첫 줄에서 화수 표기 추출 (엄격 버전).

    챕터 번호 보정/감지에 사용. 본문 **첫 비어있지 않은 줄**에서만
    "N화/N편/N장" 또는 "N. " 형태를 인정한다.
    (lib.storage._extract_chapter_num의 MULTILINE 폴백은 내용 중간의
    "N화" 언급까지 잡아 오탐을 유발하므로 여기서는 배제)
    """
    import re
    if not content:
        return None
    first = ""
    for line in content.split("\n"):
        if line.strip():
            first = line.strip()
            break
    if not first:
        return None
    m = re.match(r'^(\d{1,4})\s*(?:화|편|장)\b', first)
    if m:
        return int(m.group(1))
    m = re.match(r'^(\d{1,4})\s*[.．]\s*', first)
    if m:
        return int(m.group(1))
    return None


def _saved_content_hash_index(novel_title: str, media_type: str = "novel") -> dict:
    """소설별 저장된 본문 해시 → {chapter: 파일명} 인덱스 (중복 감지용).

    같은 본문이 서로 다른 chapter 번호로 저장되어 있으면 동일 해시 아래
    여러 (chapter, 파일명)이 나온다.
    """
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    cache_key = f"{media_type}:{novel_id}"
    if cache_key in _saved_hash_cache:
        return _saved_hash_cache[cache_key]
    from lib.paths import novel_dir_for, resolve_novel_dir
    novel_dir = novel_dir_for(novel_title, media_type)
    if not novel_dir.exists():
        novel_dir = resolve_novel_dir(novel_id)
    index: dict = {}
    if novel_dir.exists():
        for f in novel_dir.glob("*.json"):
            if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
                continue
            try:
                d = json.load(open(f, encoding='utf-8'))
                c = d.get('content') or ''
                ch = d.get('chapter')
                if not c or not isinstance(ch, int):
                    continue
                index.setdefault(_content_hash(c), {})[ch] = f.name
            except Exception:
                pass
    _saved_hash_cache[cache_key] = index
    return index


def _invalidate_saved_hash(novel_title: str) -> None:
    """저장 후 해시 인덱스 무효화."""
    novel_id = novel_title.replace(' ', '_').replace('/', '_')
    for mt in ("novel", "comic", "webtoon"):
        _saved_hash_cache.pop(f"{mt}:{novel_id}", None)


def _record_duplicate(novel_title: str, wr_id: int, chapter, dup_chapter, dup_file: str, reason: str) -> None:
    """중복 본문 이벤트를 duplicates.json에 기록 (치료 대상 파악용)."""
    try:
        records = []
        if DUPLICATES_FILE.exists():
            try:
                records = json.loads(DUPLICATES_FILE.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    records = []
            except Exception:
                records = []
        records.append({
            "novel_title": novel_title,
            "wr_id": wr_id,
            "chapter": chapter,
            "dup_chapter": dup_chapter,
            "dup_file": dup_file,
            "reason": reason,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })
        records = records[-2000:]
        DUPLICATES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _run_collect_locked(limit: int = 0, source_filter: str = "") -> dict:
    """run_collect 본체 (락 보유 상태에서 실행).

    트래픽 가드: 유료 프록시 소스(traffic_limited=True, 예: toki31)의
    일일 한도 초과 시 해당 소스만 중단. bookto31(FlareSolverr 로컬)은
    트래픽 가드와 무관하게 계속 처리된다.
    (loop는 소스별로 순회하므로 한 소스의 중단이 다른 소스를 막지 않음)
    """
    from lib.sources import get_traffic_limited
    from lib.traffic_guard import reset_if_new_day, is_exceeded, add_bytes, summary
    from lib.toki31_playwright import get_traffic_total_bytes, get_collector_state

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

        from lib.sources import get_media_type
        media_type = get_media_type(source, item.get('bo_table'))

        # 재다운로드 방지: 소스 무관 이미 저장된 chapter면 다운로드 없이 스킵
        # (bookto31/toki31이 같은 chapter를 서로 다른 wr_id로 재발견하는 경우 방지)
        chapter_num = item.get('chapter')
        if chapter_num is not None and novel_title:
            if chapter_num in _load_saved_chapters(novel_title, media_type):
                log.info(
                    f"  ↷ chapter {chapter_num} 이미 저장됨 — 다운로드 스킵 (wr_id={wr_id})"
                )
                removed_ids.add(wr_id)
                dedup_skipped += 1
                continue

        # collector 선택 (source별 분기 — collector 키는 sources.json에서 해석)
        from lib.sources import get_collector
        if media_type in ('comic', 'webtoon'):
            # 이미지 기반 콘텐츠(웹툰/만화)는 전용 이미지 수집기 사용
            collector = _collect_webtoon
        else:
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
                # 본문이 비어 있으면 사이트에 내용이 없는 것 — 재시도해도 소용없음.
                # (FlareSolverr rate limit 8분 재대기 낭비 방지 → 빈 챕터 빠른 스킵)
                body_len = len(body[1]) if isinstance(body, tuple) and len(body) == 2 else len(body or "")
                if body_len == 0:
                    log.warning("  본문 비어 있음 (사이트에 내용 없음) — 재시도 생략")
                    break
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                log.warning(f"  fetch 실패 ({attempt+1}/3): {error_msg}")
                time.sleep(2)
        fetch_elapsed = time.monotonic() - _t0
        traffic_delta = get_traffic_total_bytes() - traffic_before
        cs = get_collector_state() if source == "toki31" else {}
        state_tag = "콜드" if cs.get("is_cold") else "웜"
        if traffic_delta > 0:
            add_bytes(traffic_delta, chapter=True)
            log.info(
                f"  📊 트래픽: {traffic_delta / 1024:.1f}KB [{state_tag}] "
                f"(누적 {summary()['used_mb']:.1f}MB/{summary()['daily_limit_mb']}MB) "
                f"JS캐시={cs.get('js_cache',0)} hit={cs.get('js_hits',0)} wasm={cs.get('wasm_cache',0)}"
            )
        elif traffic_delta == 0 and success:
            log.info(f"  📊 트래픽: 0KB [{state_tag}] (전체 캐시 히트)")

        if not success:
            body_len = len(body[1]) if isinstance(body, tuple) and len(body) == 2 else len(body or "")
            # 빈 본문(사이트에 내용 없음)은 재시도 무의미 → 1회 실패로 즉시 DLQ (백필 정체 방지)
            empty_source = body_len == 0
            item['last_error'] = (
                "빈 챕터 (사이트에 본문 없음)" if empty_source
                else f"3회 시도 후 실패 (body={body_len})"
            )
            log.warning(f"  ✗ {item['last_error']}")
            if item['attempts'] >= 3 or empty_source:
                # 실패 → DLQ 기록 후 queue에서 제거 (데이터 보존)
                _add_to_dlq(item, item['last_error'])
                removed_ids.add(wr_id)
            errors.append({"wr_id": wr_id, "error": item['last_error']})
            continue

        # 저장 (enrich/index 없이 순수 저장)
        chapter_num = item.get('chapter') or chapter_num
        body_text = body[1] if isinstance(body, tuple) and len(body) == 2 else body

        # ── 중복 본문 방지 ──
        # 같은 본문이 다른 화수(chapter 번호)로 이미 저장되어 있으면 저장 생략.
        # (소스 wr_id 매핑 오류로 같은 내용이 중복 저장되는 케이스 방지)
        dup_hit = None
        if body_text and novel_title:
            idx = _saved_content_hash_index(novel_title, media_type)
            for saved_ch, saved_file in (idx.get(_content_hash(body_text)) or {}).items():
                if saved_ch != chapter_num:
                    dup_hit = (saved_ch, saved_file)
                    break
        if dup_hit:
            log.warning(
                f"  ⚠ wr_id={wr_id} 본문 중복 — chapter {dup_hit[0]} ({dup_hit[1]})와 동일, 저장 생략"
            )
            _record_duplicate(novel_title, wr_id, chapter_num, dup_hit[0], dup_hit[1], "same_body_different_chapter")
            removed_ids.add(wr_id)
            dedup_skipped += 1
            continue

        # ── 챕터 번호 보정 ──
        # 본문 표기("N화")가 기대 chapter와 다르고 충돌이 없으면 본문 표기 우선.
        # (discover의 wr_id→화수 매핑이 오프바이원일 때 저장 단계에서 바로잡음)
        claimed = None
        if body_text:
            claimed = _claimed_chapter_strict(body_text)
        if claimed and claimed != chapter_num:
            saved_set = _load_saved_chapters(novel_title, media_type)
            if claimed in saved_set:
                log.warning(
                    f"  ⚠ wr_id={wr_id} 본문 표기 {claimed}화가 이미 저장됨 — 저장 생략"
                )
                _record_duplicate(novel_title, wr_id, chapter_num, claimed, f"ch{claimed}", "claimed_already_saved")
                removed_ids.add(wr_id)
                dedup_skipped += 1
                continue
            log.warning(f"  ↷ 챕터 번호 보정: {chapter_num} → {claimed} (본문 표기)")
            chapter_num = claimed

        _save_chapter_only(novel_title, wr_id, body, chapter_num, source, media_type)
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

    # 전체 queue에서 처리/실패 제거된 항목만 제거하고 저장.
    # 동시에 실행된 discover가 추가한 항목을 보존하기 위해 디스크 최신 상태에 델타 적용.
    remaining_queue = [q for q in full_queue if q['wr_id'] not in removed_ids]
    _merge_into_queue(remove_ids=removed_ids)
    s = summary()
    log.info(f"collect 완료: {processed}개 처리, {len(remaining_queue)}개 남음")
    cs = get_collector_state() if (not source_filter or source_filter == "toki31") else {}
    avg = s['used_mb'] / max(1, s['chapters']) if s['chapters'] else 0
    log.info(
        f"  📊 트래픽 요약: {s['used_mb']:.1f}MB / {s['daily_limit_mb']}MB "
        f"({s['chapters']}화, 화당 평균 {avg:.2f}MB)"
        + (f" | JS캐시={cs.get('js_cache',0)} hit={cs.get('js_hits',0)} wasm={cs.get('wasm_cache',0)}" if cs else "")
    )

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


def _merge_into_queue(add_items: Optional[list] = None, remove_ids: Optional[set] = None) -> None:
    """디스크 큐 최신 상태에 델타를 적용해 원자적으로 저장.

    collect 루프와 discover(관리자 서브프로세스 등)가 같은 queue.json을
    읽고 쓰는 read-modify-write 경합을 방지한다. _load_queue→_save_queue가
    개별적으로만 락을 잡아 한쪽의 추가/제거가 다른 쪽의 저장에 덮이는
    버그를, 읽기~쓰기 동안 queue.lock EX를 유지해 해결한다.

    Args:
        add_items: 이번 호출에서 새로 추가할 큐 항목 (wr_id 기준 dedup, 신규 우선)
        remove_ids: 이번 호출에서 처리/제거된 wr_id 집합
    """
    import fcntl
    import threading as _threading
    add_items = add_items or []
    remove_ids = remove_ids or set()
    lockf = _queue_lock()
    fcntl.flock(lockf, fcntl.LOCK_EX)
    try:
        try:
            with open(QUEUE_FILE, encoding='utf-8') as f:
                current = json.load(f)
        except (json.JSONDecodeError, OSError):
            current = []
        by_id: dict = {}
        for it in current:
            if it.get('wr_id') not in remove_ids:
                by_id[it['wr_id']] = it
        for it in add_items:
            by_id[it['wr_id']] = it
        tmp_path = QUEUE_FILE.with_suffix(f'.tmp.{os.getpid()}.{_threading.get_ident()}')
        with open(tmp_path, 'w') as f:
            json.dump(list(by_id.values()), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, QUEUE_FILE)  # atomic rename
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)


def _save_chapter_only(novel_title: str, wr_id: int, body: str, chapter_num: Optional[int] = None, source: str = "bookto31", media_type: str = "novel") -> bool:
    """순수 저장 (enrich/index/revalidate 없이).

    body가 튜플 (title, content)이면 (newtoki/toki31 collector) content만 사용.
    media_type: 저장 폴더 결정 ("novel"|"comic"|"webtoon")
    """
    from lib.storage import save_chapter as _save

    if isinstance(body, tuple) and len(body) == 2:
        # (title, content) → content 사용
        content = body[1]
        if chapter_num is None:
            from lib.storage import _extract_chapter_num
            chapter_num = _extract_chapter_num(content)
        body = content

    save_kwargs = {"source": source, "media_type": media_type}
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

def run_check_duplicates(novel_filter: Optional[str] = None, fix: bool = False) -> dict:
    """소설별 중복 본문/챕터 번호 불일치 감지 + (--fix) 자동 수리.

    감지:
      1. 동일 본문이 서로 다른 chapter 번호로 저장된 경우 (중복)
      2. 본문 표기("N화")와 저장 chapter 번호가 다른 경우 (소스 wr_id 매핑 오프바이원)

    fix=True 수리:
      - 중복: 본문 표기와 일치하는 chapter가 canonical, 나머지 파일은 삭제 전 백업 이동
      - 불일치: 저장 chapter = 본문 표기로 재정렬 (충돌/모호 시 스킵+보고)
      - 이후 _chapters_index.json 재구축
    변경/삭제 파일은 `_dupe_backup_YYYYMMDD/`로 백업한다.

    사용법:
      pipeline.py check-dupes                    # 전체 감지 (보고만)
      pipeline.py check-dupes 화산귀환 --fix      # 특정 소설 감지 + 수리
    """
    import shutil
    from lib.paths import iter_novel_dirs, resolve_novel_dir
    from services.data import rebuild_chapters_index

    # 대상 소설 디렉토리 결정
    targets = []
    if novel_filter:
        nid = novel_filter.replace(' ', '_').replace('/', '_')
        for mt, nd in iter_novel_dirs():
            if nd.name == nid:
                targets.append(nd)
                break
        if not targets:
            nd = resolve_novel_dir(nid)
            if nd.exists():
                targets.append(nd)
    else:
        targets = [nd for mt, nd in iter_novel_dirs()]

    stats = {
        "novels": 0, "duplicates": [], "mislabeled": [],
        "renumbered": 0, "removed": 0, "collisions": [], "skipped": [],
    }

    for nd in targets:
        novel_id = nd.name
        files = [f for f in nd.glob("*.json")
                 if f.name not in ("meta.json", "_chapters_index.json") and f.stem.isdigit()]
        if not files:
            continue
        stats["novels"] += 1

        # (a) 로드 + (b) 백업 dir (fix 전용)
        bak = None
        if fix:
            from datetime import datetime as _dt
            bak = nd / f"_dupe_backup_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
            bak.mkdir(exist_ok=True)

        entries = []
        for f in files:
            try:
                d = json.load(open(f, encoding='utf-8'))
                c = d.get('content') or ''
                ch = d.get('chapter')
                if not c or not isinstance(ch, int):
                    continue
                entries.append({
                    "file": f, "wr": f.stem, "ch": ch, "claimed": _claimed_chapter_strict(c),
                    "hash": _content_hash(c), "data": d,
                })
            except Exception:
                stats["skipped"].append({"novel": novel_id, "file": f.name, "reason": "json_error"})

        # 1) 중복 본문 (같은 해시, 다른 chapter)
        by_hash: dict[str, list] = {}
        for e in entries:
            by_hash.setdefault(e["hash"], []).append(e)
        for h, group in by_hash.items():
            if len({e["ch"] for e in group}) > 1:
                stats["duplicates"].append({"novel": novel_id, "group": [(e["wr"], e["ch"], e["claimed"]) for e in group]})

        # 2) 불일치 (본문 표기 != 저장 chapter)
        for e in entries:
            if e["claimed"] and e["claimed"] != e["ch"]:
                stats["mislabeled"].append({"novel": novel_id, "wr": e["wr"], "ch": e["ch"], "claimed": e["claimed"]})

        if not fix:
            continue

        # ── fix: 재정렬 + 중복 제거 ──
        # 본문 표기(claimed)가 진실값. claimed → 파일 목록으로 그룹화.
        claimed_map: dict[int, list] = {}
        for e in entries:
            if e["claimed"]:
                claimed_map.setdefault(e["claimed"], []).append(e)

        # 정적 챕터 점유: 본문 표기가 없거나 이미 일치하는 파일 (이동하지 않음)
        static = {e["ch"]: e for e in entries if (not e["claimed"]) or e["ch"] == e["claimed"]}

        # 1) 중복 그룹 (같은 claimed, 여러 파일): canonical 유지, 나머지 백업+제거
        for claimed, group in claimed_map.items():
            if len(group) < 2:
                continue
            canon = next((e for e in group if e["ch"] == claimed), group[0])
            for e in group:
                if e is canon:
                    continue
                if e["hash"] != canon["hash"]:
                    # 같은 화수를 주장하지만 내용이 다름 = 진짜 충돌 → 수동 필요
                    stats["collisions"].append({
                        "novel": novel_id, "claimed": claimed,
                        "a": f"{e['wr']}.json(ch{e['ch']})", "b": f"{canon['wr']}.json(ch{canon['ch']})",
                        "reason": "different_content_same_claimed",
                    })
                    log.warning(f"  ⚠ [{novel_id}] 진짜 충돌: {e['wr']}.json vs {canon['wr']}.json (둘 다 {claimed}화 주장, 내용 다름) — 수동 필요")
                    continue
                if bak:
                    shutil.move(str(e["file"]), str(bak / e["file"].name))
                stats["removed"] += 1
                log.warning(f"  ✂ [{novel_id}] 중복 제거: {e['file'].name} (ch{e['ch']}, 본문 {claimed}화) → canonical {canon['file'].name}")
            if canon["ch"] != claimed:
                _set_chapter_field(canon["file"], claimed)
                stats["renumbered"] += 1
                log.warning(f"  ↷ [{novel_id}] {canon['file'].name} ch {canon['ch']} → {claimed}")

        # 2) 단일 그룹: 오프바이원 재정렬 (충돌 시 스킵+보고)
        for claimed, group in claimed_map.items():
            if len(group) != 1:
                continue
            e = group[0]
            if e["ch"] == claimed:
                continue
            clash = static.get(claimed)
            if clash is not None and clash["file"] != e["file"]:
                stats["collisions"].append({
                    "novel": novel_id, "wr": e["wr"], "ch": e["ch"], "claimed": claimed,
                    "clash": f"{clash['wr']}.json",
                })
                log.warning(f"  ⚠ [{novel_id}] 충돌로 스킵: {e['file'].name} ch{e['ch']}→{claimed} (이미 {clash['file'].name}이 ch{claimed} 점유)")
                continue
            _set_chapter_field(e["file"], claimed)
            stats["renumbered"] += 1
            log.warning(f"  ↷ [{novel_id}] {e['file'].name} ch {e['ch']} → {claimed} (본문 표기)")

        # 인덱스 재구축
        if stats["renumbered"] or stats["removed"]:
            try:
                n = rebuild_chapters_index(nd)
                log.info(f"  ✓ [{novel_id}] 인덱스 재구축 ({len(n)}개)")
            except Exception as ex:
                log.warning(f"  ✗ [{novel_id}] 인덱스 재구축 실패: {ex}")

    # ── 보고 ──
    print(f"소설 {stats['novels']}개 스캔")
    print(f"  중복 본문 그룹: {len(stats['duplicates'])}")
    for d in stats['duplicates']:
        print(f"    - {d['novel']}: {d['group']}")
    print(f"  챕터 번호 불일치: {len(stats['mislabeled'])}")
    if not fix:
        for m in stats['mislabeled'][:30]:
            print(f"    - {m['novel']} {m['wr']}.json ch{m['ch']} → 본문 {m['claimed']}화")
        if len(stats['mislabeled']) > 30:
            print(f"    ... 외 {len(stats['mislabeled'])-30}건")
    if fix:
        print(f"  수리: 재정렬 {stats['renumbered']}건, 중복 제거 {stats['removed']}건")
        if stats['collisions']:
            print(f"  충돌 스킵: {len(stats['collisions'])}건")
            for c in stats['collisions'][:20]:
                print(f"    - {c}")
        if stats['skipped']:
            print(f"  스킵(오류): {len(stats['skipped'])}건")
    return stats


MISSING_FILE = WATCHER_DIR / 'missing.json'


def _load_missing() -> dict:
    """missing.json 로드 (소설별 빠진 화수 목록)."""
    if MISSING_FILE.exists():
        try:
            d = json.loads(MISSING_FILE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _save_missing(missing: dict) -> None:
    """missing.json 저장."""
    try:
        MISSING_FILE.write_text(json.dumps(missing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"missing.json 저장 실패: {e}")


def run_check_gaps(novel_filter: Optional[str] = None) -> dict:
    """소설별 빠진 화수(챕터 번호 누락) 감지 → missing.json 기록.

    - 1~최대화수 사이에서 저장되지 않은 화수와 큐에 없는 화수를 누락으로 판정
    - DLQ의 "빈 챕터"는 reason="empty_source", 그 외는 reason="gap"
    사용법:
      pipeline.py check-gaps            # 전체 감지
      pipeline.py check-gaps 화산귀환    # 특정 소설
    """
    from lib.paths import iter_novel_dirs, resolve_novel_dir

    targets = []
    if novel_filter:
        nid = novel_filter.replace(' ', '_').replace('/', '_')
        for mt, nd in iter_novel_dirs():
            if nd.name == nid:
                targets.append(nd)
                break
        if not targets:
            nd = resolve_novel_dir(nid)
            if nd.exists():
                targets.append(nd)
    else:
        targets = [nd for mt, nd in iter_novel_dirs()]

    queue = _load_queue()
    queue_chapters: dict[str, set] = {}
    for it in queue:
        nt = (it.get('novel_title') or '').replace(' ', '_').replace('/', '_')
        queue_chapters.setdefault(nt, set()).add(it.get('chapter'))

    # DLQ에서 빈 챕터 확인
    dlq_empty: dict[int, str] = {}
    if DLQ_FILE.exists():
        try:
            for r in json.loads(DLQ_FILE.read_text(encoding="utf-8")):
                if '빈 챕터' in (r.get('error') or ''):
                    dlq_empty[r.get('wr_id')] = r.get('chapter')
        except Exception:
            pass

    missing = _load_missing()
    changed = False
    for nd in targets:
        novel_id = nd.name
        chs = set()
        for f in nd.glob("*.json"):
            if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
                continue
            try:
                ch = json.load(open(f, encoding='utf-8')).get('chapter')
                if isinstance(ch, int) and ch > 0:
                    chs.add(ch)
            except Exception:
                pass
        if not chs:
            continue
        max_ch = max(chs)
        queued = queue_chapters.get(novel_id, set())
        novel_missing = {}
        for c in range(1, max_ch + 1):
            if c in chs or c in queued:
                continue
            reason = "empty_source" if c in set(dlq_empty.values()) else "gap"
            novel_missing[str(c)] = {
                "reason": reason,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
        if novel_missing != (missing.get(novel_id) or {}):
            missing[novel_id] = novel_missing
            changed = True
    if changed:
        _save_missing(missing)

    print(f"감지 완료 — 빠진 화수 소설 {sum(1 for v in missing.values() if v)}개")
    for nid, mm in missing.items():
        if not mm:
            continue
        reasons = {}
        for c, info in mm.items():
            reasons[info.get('reason', 'gap')] = reasons.get(info.get('reason', 'gap'), 0) + 1
        print(f"  {nid}: {len(mm)}개 ({reasons})")
        gap_chs = [int(c) for c, info in mm.items() if info.get('reason') == 'gap']
        empty_chs = [int(c) for c, info in mm.items() if info.get('reason') == 'empty_source']
        if gap_chs:
            print(f"    누락(gap): {sorted(gap_chs)[:20]}{' ...' if len(gap_chs) > 20 else ''}")
        if empty_chs:
            print(f"    빈 챕터(source): {sorted(empty_chs)}")
    return missing


def run_retry_missing(novel_filter: Optional[str] = None, dry_run: bool = False) -> int:
    """missing.json의 빠진 화수를 재발견 → 큐 재등록 (정확한 wr_id로).

    누락/빈 챕터를 소스에서 다시 discover해 올바른 wr_id로 큐에 넣는다.
    (wr_id를 화수로 위조하지 않음 — 소스 페이지에서 실제 wr_id를 찾는다)

    사용법:
      pipeline.py retry-missing                # 전체
      pipeline.py retry-missing 화산귀환        # 특정 소설
      pipeline.py retry-missing --dry-run      # 대상 소설 미리보기
    """
    from lib.paths import resolve_novel_dir

    missing = _load_missing()
    re_discovered = 0
    for nid, mm in missing.items():
        if novel_filter and novel_filter.replace(' ', '_').replace('/', '_') != nid:
            continue
        if not mm:
            continue
        nd = resolve_novel_dir(nid)
        meta = {}
        if nd.exists() and (nd / 'meta.json').exists():
            try:
                meta = json.loads((nd / 'meta.json').read_text(encoding='utf-8'))
            except Exception:
                pass
        source = meta.get('source') or 'bookto31'
        main_wr_id = meta.get('main_wr_id')
        title = meta.get('title') or nid.replace('_', ' ')
        if dry_run:
            log.info(f"  [dry] {nid}: {len(mm)}화 재발견 대상 (source={source}, main_wr_id={main_wr_id})")
            continue
        if not main_wr_id:
            log.warning(f"  {nid}: main_wr_id 없음 — discover 불가 (수동으로 wr_id 지정 필요)")
            continue
        # gnuboard(bookto31 계열)만 자동 재발견 지원
        from lib.sources import get_discover
        if get_discover(source) != "gnuboard":
            log.warning(f"  {nid}: source={source}는 자동 재발견 미지원 (수동 필요)")
            continue
        log.info(f"  ↻ {nid}: 재발견 시작 ({len(mm)}화 누락)")
        try:
            added = run_discover(main_wr_id, title, source=source, max_pages=200)
            re_discovered += added
        except Exception as e:
            log.warning(f"  {nid} 재발견 실패: {e}")
    log.info(f"재발견 완료: {re_discovered}건 큐에 추가")
    return re_discovered


def _set_chapter_field(file_path: Path, chapter: int) -> None:
    """챕터 JSON의 chapter/제목 필드를 갱신 (재정렬용)."""
    import re as _re
    try:
        d = json.load(open(file_path, encoding='utf-8'))
    except Exception:
        return
    d['chapter'] = chapter
    title = d.get('title') or ""
    m = _re.sub(r'(\s*\d+\s*(?:화|편|장)\s*)$', f' {chapter}화', title)
    if m != title:
        d['title'] = m
    tmp = file_path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.rename(file_path)


def run_all(novel_main_wr_id: int, novel_title: str, source: str = "bookto31", bo_table: str = "novel") -> dict:
    """전체 파이프라인 실행 (discover → collect → enrich → index → revalidate)."""
    results = {}

    log.info("=" * 50)
    log.info(f"파이프라인 시작 (source={source}, bo_table={bo_table})")
    log.info("=" * 50)

    # 1. discover
    log.info("\n[1/5] DISCOVER — 회차 발견")
    added = run_discover(novel_main_wr_id, novel_title, source=source, bo_table=bo_table)
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
        bo_table = meta.get('bo_table') or 'novel'
        try:
            added = run_discover(int(main_wr_id), title, max_pages=200, source=source, bo_table=bo_table)
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
            print("사용법: pipeline.py discover <wr_id> [novel_title] [max_pages] [--source bookto31|newtoki] [--bo-table fafa19] [--dry-run]")
            return 1
        wr_id = int(sys.argv[2])
        source = _parse_source()
        bo_table = _parse_bo_table()
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
        run_discover(wr_id, title, pages, source, dry_run, bo_table)

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

    elif cmd == "check-dupes":
        """중복 본문/챕터 번호 불일치 감지 + (--fix) 자동 수리.
        사용법:
          pipeline.py check-dupes                 # 전체 감지 (보고만)
          pipeline.py check-dupes 화산귀환         # 특정 소설 감지
          pipeline.py check-dupes 화산귀환 --fix   # 감지 + 수리(재정렬/중복 제거)
        """
        args = sys.argv[2:]
        fix = "--fix" in args
        novel_filter = next((a for a in args if not a.startswith("--")), None)
        run_check_duplicates(novel_filter, fix=fix)

    elif cmd == "check-gaps":
        """소설별 빠진 화수 감지 → missing.json 기록.
        사용법: pipeline.py check-gaps [소설명]
        """
        novel_filter = sys.argv[2] if len(sys.argv) > 2 else None
        run_check_gaps(novel_filter)

    elif cmd == "retry-missing":
        """missing.json의 빠진 화수를 소스에서 재발견 → 큐 재등록.
        사용법: pipeline.py retry-missing [소설명] [--dry-run]
        """
        args = sys.argv[2:]
        dry_run = "--dry-run" in args
        novel_filter = next((a for a in args if not a.startswith("--")), None)
        run_retry_missing(novel_filter, dry_run=dry_run)

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
            print("사용법: pipeline.py all <wr_id> <novel_title> [--source bookto31|newtoki] [--bo-table fafa19]")
            return 1
        wr_id = int(sys.argv[2])
        title = sys.argv[3]
        source = _parse_source()
        bo_table = _parse_bo_table()
        run_all(wr_id, title, source, bo_table)

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
                # 기존 domain_health 등 필드는 유지하고 phase/cycle만 갱신
                try:
                    prev_status = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {}
                    if not isinstance(prev_status, dict):
                        prev_status = {}
                except Exception:
                    prev_status = {}
                prev_status.update({"phase": "loop", "cycle": cycle, "source": source or "all"})
                _write_status(prev_status)
                # systemd watchdog 신호 (WatchdogSec 대응)
                _sd_notify(f"cycle {cycle}")

                # 도메인 헬스체크 (30분 간격, 사이트 주소 변경 시 자동 전환)
                try:
                    _check_domain_health_once()
                except Exception as e:
                    log.warning(f"도메인 헬스체크 실패: {e}")

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
                        # 실패한 웹툰 이미지 재다운로드 체크 (CDN 복구 대비)
                        try:
                            retry_failed_webtoon_images()
                        except Exception as e:
                            log.warning(f"웹툰 이미지 재시도 실패: {e}")
                        # 누락 화수 추적 갱신 (missing.json) — 소스가 채워진 누락/빈 챕터 파악
                        try:
                            run_check_gaps()
                        except Exception as e:
                            log.warning(f"check-gaps 실패: {e}")
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