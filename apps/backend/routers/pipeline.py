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
  bookto31: https://bookto31.com/bbs/board.php?bo_table=novel&wr_id=25575
  newtoki:  https://toki31.com/novel/58455
"""

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.data import resolve_status

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

URL_PATTERNS = [
    # bookto31: .../bbs/board.php?bo_table=novel&wr_id=25575
    (r"bookto31\.com.*wr_id=(\d+)", "bookto31"),
    # newtoki/toki31: .../novel/58455
    (r"(?:newtoki|toki)\w*\.com/novel/(\d+)", "newtoki"),
]


def parse_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """URL에서 source와 ID 추출.

    Returns:
        (source, id) 또는 (None, None)
    """
    for pat, source in URL_PATTERNS:
        m = re.search(pat, url)
        if m:
            return source, m.group(1)
    return None, None


# ============================================================
# 백그라운드 작업 — 진행 중인 작업 추적
# ============================================================

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _extract_title(source: str, wr_id: str) -> str:
    """제목 추출 (discover --dry-run 한 번만 실행)."""
    script = str(PIPELINE_SCRIPT)
    try:
        result = subprocess.run(
            ["python3", script, "discover", wr_id, "--dry-run", "--source", source],
            capture_output=True, text=True, timeout=60,
        )
        for line in result.stdout.split("\n"):
            if line.startswith("TITLE:"):
                return line.replace("TITLE:", "").strip()
    except Exception as e:
        log.warning(f"제목 추출 오류: {e}")
    return f"소설 {wr_id}"


def _run_pipeline_job(source: str, novel_id: str) -> None:
    """백그라운드: 제목 추출 → discover 큐 등록 → loop 시작."""
    script = str(PIPELINE_SCRIPT)

    def _update(**kw):
        with _JOBS_LOCK:
            if novel_id in _JOBS:
                _JOBS[novel_id].update(kw)

    _update(status="제목 추출 중")
    title = _extract_title(source, novel_id)
    _update(title=title)

    _update(status="회차 탐색 중")
    try:
        # 전체 회차 확인 (max_pages 200) — 시간이 걸릴 수 있어 timeout 넉넉히
        result = subprocess.run(
            ["python3", script, "discover", novel_id, title, "200", "--source", source],
            capture_output=True, text=True, timeout=1500,
        )
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


NOVELS_DIR = Path("/opt/ai_data/flaresolverr/novels")


def _estimate_seconds_per_chapter(novel_dir: Path, fallback: int = 300) -> int:
    """최근 수집 간격으로 '챕터당 소요시간(초)' 추정.

    최근 5개 챕터의 collected_at 간격 평균. 데이터 없으면 fallback.
    bookto31은 적응형 딜레이(300~600초), toki31은 5~60초.
    """
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
    if len(stamps) < 2:
        return fallback
    stamps.sort()
    from datetime import datetime
    try:
        times = [datetime.fromisoformat(t) for t in stamps[-6:]]
        gaps = [
            (times[i + 1] - times[i]).total_seconds()
            for i in range(len(times) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            return fallback
        avg = sum(gaps) / len(gaps)
        return int(max(10, min(avg, 3600)))
    except Exception:
        return fallback


def get_novel_status() -> list[dict]:
    """모든 소설의 저장 상태 + 수집 완료 여부 목록.

    - collection_done: queue가 비어 있고 saved == total (수집 작업 완료)
    - status: 완결/연재중/단편 (메타데이터 기반 + fallback 추론)
    - eta_seconds: 수집 중(collection_done=False)일 때 남은 예상 시간(초)
    """
    novels = []
    try:
        if not NOVELS_DIR.exists():
            return novels
        # queue에 남아있는 작품명 목록
        queued_titles = set()
        if QUEUE_FILE.exists():
            try:
                with open(QUEUE_FILE, encoding="utf-8") as f:
                    for item in json.load(f):
                        if item.get("novel_title"):
                            queued_titles.add(item["novel_title"])
            except Exception:
                pass
        for novel_dir in sorted(NOVELS_DIR.iterdir()):
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
            # totalChapters가 부정확(1 등)하거나, queue에 회차가 있으면
            # 실제 대상 회차 수 = 저장된 수 + 큐 대기 수로 계산
            meta_total = meta.get("totalChapters") or 0
            q_count = 0
            if queued and QUEUE_FILE.exists():
                try:
                    with open(QUEUE_FILE, encoding="utf-8") as f:
                        q_count = sum(1 for it in json.load(f) if it.get("novel_title") == title)
                except Exception:
                    pass
            if queued:
                total = saved + q_count
            else:
                total = meta_total if meta_total >= saved else saved
            collection_done = (not queued) and (total > 0) and (saved >= total)
            eta_seconds = None
            if not collection_done and q_count > 0:
                per = _estimate_seconds_per_chapter(novel_dir)
                eta_seconds = q_count * per
            novels.append({
                "id": novel_dir.name,
                "title": title,
                "saved": saved,
                "total": total,
                "status": status,
                "queued": queued,
                "collection_done": collection_done,
                "eta_seconds": eta_seconds,
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

    # 2. URL 파싱
    source, novel_id = parse_url(req.url)
    if not source or not novel_id:
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 URL 형식입니다. bookto31.com 또는 toki31.com URL이어야 합니다.",
        )

    # 3. 중복 시작 방지
    with _JOBS_LOCK:
        if novel_id in _JOBS and _JOBS[novel_id]["status"] != "완료":
            return StartPipelineResponse(
                ok=True,
                source=source,
                novel_id=novel_id,
                title=_JOBS[novel_id].get("title", ""),
                message="이미 파이프라인 작업이 진행 중입니다",
                queue_stats=get_queue_stats(),
                loop_running=is_loop_running(),
            )
        _JOBS[novel_id] = {
            "source": source,
            "novel_id": novel_id,
            "title": "",
            "status": "시작 중",
            "message": "",
        }

    # 4. 백그라운드 실행 후 즉시 응답
    threading.Thread(target=_run_pipeline_job, args=(source, novel_id), daemon=True).start()

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