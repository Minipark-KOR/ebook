#!/usr/bin/env python3
# Status: experimental
# Path: none — admin gateway
"""파이프라인 관문 API — URL 입력 → source/ID 추출 → 파이프라인 실행.

Admin 페이지에서 URL을 받아:
1. 비밀번호 검증
2. URL 파싱 (source + ID 자동 분기)
3. 작품 메인 페이지 fetch → 제목 추출
4. pipeline.py discover 실행 → 큐 등록
5. pipeline.py loop가 실행 중인지 확인, 없으면 시작

URL 패턴:
  bookto31: https://23.ondobook.net/bbs/board.php?bo_table=novel&wr_id=25575
  newtoki:  https://toki31.com/novel/58455
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.data import resolve_status
from lib.paths import MEDIA_DIRS, iter_novel_dirs

log = logging.getLogger("pipeline_router")

router = APIRouter()

# 비밀번호 (환경변수 또는 기본값)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "0107460416")

# 파이프라인 스크립트 경로
PIPELINE_SCRIPT = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "pipeline.py"
WATCHER_DIR = Path("/opt/ai_data/flaresolverr/ebook_watcher")
QUEUE_FILE = WATCHER_DIR / "queue.json"


# ============================================================
# URL 파싱 — source + ID 자동 분기
# ============================================================

def parse_url(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """URL에서 source, ID, bo_table 추출.

    소스 레지스트리(sources.json)의 domains로 매칭.
    각 소스별 ID 추출 패턴은 소스 특성에 따라 분기:
      - bookto31(GNUBOARD): wr_id=N (+ bo_table)
      - toki31(newtoki): /novel/{id}
    bo_table: gnuboard 게시판(콘텐츠 종류) — discover/collect에서 그대로 사용.
    Returns:
        (source, id, bo_table) 또는 (None, None, None)
    """
    from lib.sources import get_source_from_url, get_discover

    source = get_source_from_url(url)
    if not source:
        return None, None, None
    discover = get_discover(source)
    if discover == "toki31_episodes":
        m = re.search(r"/novel/(\d+)", url)
        return (source, m.group(1), None) if m else (None, None, None)
    # 기본 GNUBOARD (bookto31 계열): wr_id=N + bo_table
    m = re.search(r"wr_id=(\d+)", url)
    if not m:
        return None, None, None
    bo_m = re.search(r"bo_table=([A-Za-z0-9_]+)", url)
    bo_table = bo_m.group(1) if bo_m else "novel"
    return (source, m.group(1), bo_table)


# ============================================================
# 백그라운드 작업 — 진행 중인 작업 추적
# ============================================================

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _extract_title(source: str, wr_id: str, bo_table: str = "novel") -> str:
    """제목 추출 (discover --dry-run 한 번만 실행)."""
    script = str(PIPELINE_SCRIPT)
    try:
        cmd = ["python3", script, "discover", wr_id, "--dry-run", "--source", source]
        if bo_table:
            cmd += ["--bo-table", bo_table]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for line in result.stdout.split("\n"):
            if line.startswith("TITLE:"):
                return line.replace("TITLE:", "").strip()
    except Exception as e:
        log.warning(f"제목 추출 오류: {e}")
    return f"소설 {wr_id}"


def _run_pipeline_job(source: str, novel_id: str, bo_table: str = "novel") -> None:
    """백그라운드: 제목 추출 → discover 큐 등록 → loop 시작."""
    script = str(PIPELINE_SCRIPT)
    job_key = f"{bo_table or 'novel'}:{novel_id}"

    def _update(**kw):
        with _JOBS_LOCK:
            if job_key in _JOBS:
                _JOBS[job_key].update(kw)

    _update(status="제목 추출 중")
    title = _extract_title(source, novel_id, bo_table)
    _update(title=title)

    _update(status="회차 탐색 중")
    try:
        # 전체 회차 확인 (max_pages 200) — 시간이 걸릴 수 있어 timeout 넉넉히
        cmd = ["python3", script, "discover", novel_id, title, "200", "--source", source]
        if bo_table:
            cmd += ["--bo-table", bo_table]
        result = subprocess.run(cmd, timeout=600, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning(f"discover 오류: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        log.warning("discover 타임아웃")
    except Exception as e:
        log.error(f"discover 실행 실패: {e}")

    if not is_loop_running():
        try:
            log_path = WATCHER_DIR / "pipeline_output.log"
            subprocess.Popen(
                ["nohup", "python3", script, "loop", title, "--source", source],
                stdout=open(log_path, "a"),
                stderr=subprocess.STDOUT,
                preexec_fn=os.setpgrp,
            )
            _update(message="파이프라인 루프를 시작했습니다")
        except Exception as e:
            _update(message=f"루프 시작 실패: {e}")
    else:
        _update(message="파이프라인 루프가 이미 실행 중입니다")

    _update(status="완료")


# ============================================================
# 큐 상태 확인
# ============================================================

def is_loop_running() -> bool:
    """pipeline loop 프로세스가 실행 중인지 확인."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "pipeline.py loop"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_queue_stats() -> dict:
    """큐 통계."""
    if not QUEUE_FILE.exists():
        return {"total": 0, "by_source": {}, "next_item": None}
    try:
        with open(QUEUE_FILE) as f:
            queue = json.load(f)
        by_source = {}
        by_novel = {}
        for item in queue:
            s = item.get("source", "bookto31")
            by_source[s] = by_source.get(s, 0) + 1
            t = item.get("novel_title") or "(제목 없음)"
            by_novel[t] = by_novel.get(t, 0) + 1
        next_item = None
        if queue:
            head = queue[0]
            next_item = {
                "wr_id": head.get("wr_id"),
                "novel_title": head.get("novel_title"),
                "chapter": head.get("chapter"),
                "source": head.get("source", "bookto31"),
            }
        return {
            "total": len(queue),
            "by_source": by_source,
            "by_novel": by_novel,
            "next_item": next_item,
        }
    except Exception:
        return {"total": 0, "by_source": {}, "next_item": None}


# ETA 추정 TTL 캐시 (novel_dir.name → (result, ts))
_ETA_CACHE: dict = {}
_ETA_CACHE_TS: dict = {}


def _estimate_seconds_per_chapter(novel_dir: Path, source: str = "bookto31") -> int:
    """'챕터당 소요시간(초)' 추정 — 수집 소스 기준.

    최근 6개 챕터의 collected_at 간격 평균을 측정하되, 소스의 speed_hint(sources.json)를
    상한으로 적용한다 (toki31 ~30초, bookto31 ~300초 등). 이전 소스 시절 파일이 섞여 있어도
    앞으로 받을 소스 속도로 계산해야 하므로 상한을 적용한다.

    TTL 캐시(60초): /pipeline/status가 3초마다 폴링하므로 파일 전체 읽기를 줄인다.
    """
    from lib.sources import get_speed_hint

    hint = get_speed_hint(source)
    key = f"{novel_dir.name}:{source}"
    now = time.time()
    if now - _ETA_CACHE_TS.get(key, 0) < 60:
        return _ETA_CACHE.get(key, hint)
    stamps = []
    for f in novel_dir.glob("*.json"):
        if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            t = (d.get("collected_at") or "").strip()
            if t:
                if t.endswith("Z"):
                    t = t[:-1] + "+00:00"
                stamps.append(t)
        except Exception:
            continue
    measured = None
    if len(stamps) >= 2:
        stamps.sort()
        from datetime import datetime
        try:
            times = [datetime.fromisoformat(t) for t in stamps[-6:]]
            gaps = [
                (times[i + 1] - times[i]).total_seconds()
                for i in range(len(times) - 1)
            ]
            gaps = [g for g in gaps if g > 0]
            if gaps:
                measured = sum(gaps) / len(gaps)
        except Exception:
            pass
    if measured is not None:
        # 측정값이 소스 상한(hint×4)보다 크면(옛 소스 잔재) 상한으로 캡.
        # hint×4: toki31 ≈ 20초(fetch 12~20 + 딜레이 5), bookto31 ≈ 1200초
        result = max(10, min(measured, float(hint) * 4))
    else:
        result = hint
    result = int(result)
    _ETA_CACHE[key] = result
    _ETA_CACHE_TS[key] = now
    return result


def get_novel_status() -> list[dict]:
    """모든 소설의 저장 상태 + 수집 완료 여부 목록.

    - collection_done: queue가 비어 있고 saved == total (수집 작업 완료)
    - status: 완결/연재중/단편 (메타데이터 기반 + fallback 추론)
    - eta_seconds: 수집 중일 때 남은 예상 시간(초). 큐 순서(FIFO) 반영.
      예: 화산귀환 앞에 게임이 queue에 있으면 게임까지 다 받은 뒤 화산이 끝난다.
    """
    novels = []
    try:
        if not any(d.exists() for d in MEDIA_DIRS.values()):
            return novels
        # queue 1회 로드
        queue_items = []
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, encoding="utf-8") as f:
                    queue_items = json.load(f)
            except Exception:
                queue_items = []
        queued_titles = {it.get("novel_title") for it in queue_items if it.get("novel_title")}

        # 큐 순서로 소설별 대기 수 + 소스 (FIFO 처리 반영)
        from collections import OrderedDict
        novel_q = OrderedDict()
        for it in queue_items:
            t = it.get("novel_title")
            if not t:
                continue
            info = novel_q.setdefault(t, {"count": 0, "source": it.get("source", "bookto31")})
            info["count"] += 1

        # 큐 순서 누적 ETA (앞 소설까지 다 받아야 뒤 소설 시작)
        cumulative = 0.0
        eta_by_title: dict = {}
        for t, info in novel_q.items():
            novel_dir = None
            for _mt, cand in iter_novel_dirs():
                if cand.name.replace("_", " ") == t:
                    novel_dir = cand
                    break
            per = _estimate_seconds_per_chapter(novel_dir, source=info["source"]) if novel_dir else 30
            cumulative += info["count"] * per
            eta_by_title[t] = int(cumulative)

        for media_type, novel_dir in iter_novel_dirs():
            if not novel_dir.is_dir() or novel_dir.name.startswith("."):
                continue
            meta_file = novel_dir / "meta.json"
            meta = {}
            if meta_file.exists():
                try:
                    with open(meta_file, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            # 저장된 챕터 수 (meta.json, 인덱스 캐시 제외)
            saved = 0
            for f in novel_dir.glob("*.json"):
                if f.name in ("meta.json", "_chapters_index.json"):
                    continue
                saved += 1
            title = meta.get("title") or novel_dir.name.replace("_", " ")
            status = resolve_status(meta, novel_dir)
            queued = title in queued_titles
            meta_total = meta.get("totalChapters") or 0
            q_count = novel_q.get(title, {}).get("count", 0)
            if queued:
                total = saved + q_count
            else:
                total = meta_total if meta_total >= saved else saved
            collection_done = (not queued) and (total > 0) and (saved >= total)
            novels.append({
                "id": novel_dir.name,
                "title": title,
                "media_type": media_type,
                "saved": saved,
                "total": total,
                "status": status,
                "queued": queued,
                "collection_done": collection_done,
                "eta_seconds": eta_by_title.get(title) if not collection_done else None,
            })
    except Exception as e:
        log.warning(f"novel status 조회 실패: {e}")
    return novels


def get_progress() -> dict:
    """pipeline.py가 status.json에 기록한 진행 상황 조회."""
    status_file = WATCHER_DIR / "status.json"
    if not status_file.exists():
        return {}
    try:
        with open(status_file) as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================
# API 엔드포인트
# ============================================================

class StartPipelineRequest(BaseModel):
    password: str
    url: str


class StartPipelineResponse(BaseModel):
    ok: bool
    source: str = ""
    novel_id: str = ""
    title: str = ""
    message: str = ""
    queue_stats: dict = {}
    loop_running: bool = False


@router.post("/pipeline/start", response_model=StartPipelineResponse)
async def start_pipeline(req: StartPipelineRequest):
    """파이프라인 시작 — URL 입력 → 자동 분기 → 백그라운드 실행.

    Admin 페이지에서 호출. 즉시 응답, 실제 작업은 백그라운드 스레드로.
    """
    # 1. 비밀번호 검증
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="비밀번호가 일치하지 않습니다")

    # 제출 URL 기록 (도메인 수시 변경 추적/분석용)
    log.info("pipeline/start 요청 URL: %s", req.url)

    # 2. URL 파싱
    source, novel_id, bo_table = parse_url(req.url)
    if not source or not novel_id:
        # 도메인은 자주 바뀌므로 미등록 도메인도 분석해서 자동 등록 시도
        # (URL 패턴 + HTML 구조로 소스 패밀리 판별 → sources.json 등록)
        try:
            from lib.domain_router import detect_and_register_source
            registered = detect_and_register_source(req.url)
            if registered:
                log.info("미등록 도메인 자동 등록 성공: %s → %s", req.url, registered)
                source, novel_id, bo_table = parse_url(req.url)
        except Exception:
            pass
    if not source or not novel_id:
        # 도메인은 자주 바뀌므로 목록을 명시하지 않는다 (sources.json 기준 자동 판정)
        log.warning("pipeline/start URL 파싱 실패: %s", req.url)
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 URL 형식입니다. sources.json에 등록된 도메인의 작품 URL이어야 합니다.",
        )
    log.info("pipeline/start 파싱 완료: url=%s → source=%s id=%s bo=%s", req.url, source, novel_id, bo_table)

    # 3. 중복 시작 방지
    with _JOBS_LOCK:
        job_key = f"{bo_table or 'novel'}:{novel_id}"
        if job_key in _JOBS and _JOBS[job_key]["status"] != "완료":
            return StartPipelineResponse(
                ok=True,
                source=source,
                novel_id=novel_id,
                title=_JOBS[job_key].get("title", ""),
                message="이미 파이프라인 작업이 진행 중입니다",
                queue_stats=get_queue_stats(),
                loop_running=is_loop_running(),
            )
        _JOBS[job_key] = {
            "source": source,
            "novel_id": novel_id,
            "bo_table": bo_table,
            "title": "",
            "status": "시작 중",
            "message": "",
        }

    # 4. 백그라운드 실행 후 즉시 응답
    threading.Thread(target=_run_pipeline_job, args=(source, novel_id, bo_table), daemon=True).start()

    return StartPipelineResponse(
        ok=True,
        source=source,
        novel_id=novel_id,
        title="",
        message="파이프라인 작업을 시작했습니다. 진행 상황은 아래에서 확인하세요.",
        queue_stats=get_queue_stats(),
        loop_running=is_loop_running(),
    )


@router.get("/pipeline/status")
async def pipeline_status():
    """파이프라인 상태 조회."""
    with _JOBS_LOCK:
        current_job = None
        for job in _JOBS.values():
            if job["status"] != "완료":
                current_job = dict(job)
                break
        jobs = [dict(j) for j in _JOBS.values()]
    return {
        "loop_running": is_loop_running(),
        "queue": get_queue_stats(),
        "progress": get_progress(),
        "novels": get_novel_status(),
        "current_job": current_job,
        "jobs": jobs,
    }