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

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
        result = subprocess.run(
            ["python3", script, "discover", novel_id, title, "50", "--source", source],
            capture_output=True, text=True, timeout=120,
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
        return {"total": 0, "by_source": {}}
    try:
        with open(QUEUE_FILE) as f:
            queue = json.load(f)
        by_source = {}
        for item in queue:
            s = item.get("source", "bookto31")
            by_source[s] = by_source.get(s, 0) + 1
        return {"total": len(queue), "by_source": by_source}
    except Exception:
        return {"total": 0, "by_source": {}}


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
            detail=f"지원하지 않는 URL 형식입니다. bookto31.com 또는 toki31.com URL이어야 합니다.",
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
        "current_job": current_job,
        "jobs": jobs,
    }