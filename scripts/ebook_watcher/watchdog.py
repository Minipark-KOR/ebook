#!/usr/bin/env python3
"""ebooklib 워처 - 큐 관리 + ebook_worker 실행 트리거.

watchdog.py가 주기적으로:
1. status.json 확인 - ebook_worker가 실행 중인지 (lock file 확인)
2. 큐에 새 작업이 있는지
3. 큐가 있고 락이 없으면 ebook_worker 실행
4. svc.pod / FlareSolverr 상태 모니터링

systemd 타이머로 1분마다 트리거.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

WATCHER_DIR = Path('/opt/ai_data/flaresolverr/ebook_watcher')
WATCHER_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE = WATCHER_DIR / 'queue.json'
STATUS_FILE = WATCHER_DIR / 'status.json'
LOCK_FILE = WATCHER_DIR / 'worker.lock'

# 워처가 ebook_worker를 트리거하는 주기
TRIGGER_INTERVAL_SEC = 60  # 1분마다 체크

# 마지막 실행 후 최소 시간
MIN_RUN_INTERVAL_SEC = 60


def is_worker_running() -> bool:
    """워커 락 파일 존재 여부로 실행 중 확인."""
    if not LOCK_FILE.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(LOCK_FILE.stat().st_mtime, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - mtime).total_seconds()
        # 락이 30분 이상 오래됐으면 stale
        if age > 1800:
            LOCK_FILE.unlink()
            return False
        return True
    except Exception:
        return False


def get_status() -> dict:
    """상태 파일 로드."""
    if not STATUS_FILE.exists():
        return {}
    try:
        with open(STATUS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_queue_size() -> int:
    """큐 크기 확인."""
    if not QUEUE_FILE.exists():
        return 0
    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            return len(json.load(f))
    except (json.JSONDecodeError, OSError):
        return 0


def check_flaresolverr_health() -> bool:
    """FlareSolverr 헬스체크."""
    try:
        r = requests.get("http://127.0.0.1:8191/health", timeout=5)
        return r.status_code == 200 and "ok" in r.text.lower()
    except Exception:
        return False


def restart_flaresolverr_if_needed() -> bool:
    """FlareSolverr 응답 없으면 재시작."""
    if check_flaresolverr_health():
        return True

    print(f"[{datetime.now()}] FlareSolverr unhealthy, restarting...")
    try:
        subprocess.run(
            ["systemctl", "--user", "restart", "svc-pod", "container-flaresolverr"],
            check=False, timeout=60,
        )
        time.sleep(10)
        if check_flaresolverr_health():
            print(f"[{datetime.now()}] FlareSolverr 복구됨")
            return True
        else:
            print(f"[{datetime.now()}] FlareSolverr 복구 실패")
            return False
    except Exception as e:
        print(f"[{datetime.now()}] FlareSolverr 재시작 오류: {e}")
        return False


def run_worker() -> bool:
    """ebook_worker 실행."""
    try:
        result = subprocess.run(
            [
                "/opt/workspace/ebooklib/apps/backend/venv/bin/python3",
                "/opt/workspace/ebooklib/scripts/ebook_watcher/ebook_worker.py"
            ],
            capture_output=True,
            text=True,
            timeout=3600,  # 최대 1시간
        )
        if result.returncode == 0:
            print(f"[{datetime.now()}] worker 완료")
        else:
            print(f"[{datetime.now()}] worker 실패 (rc={result.returncode}):")
            print(result.stderr[-500:] if result.stderr else "")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[{datetime.now()}] worker 타임아웃 (1시간)")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] worker 실행 오류: {e}")
        return False


def main():
    """워처 메인 루프."""
    print(f"[{datetime.now()}] ebook_watcher 시작")

    # FlareSolverr 헬스체크 (실패 시 재시작)
    if not restart_flaresolverr_if_needed():
        print(f"[{datetime.now()}] FlareSolverr 복구 실패 - 1분 후 재시도")
        return 1

    # 큐 상태
    queue_size = get_queue_size()
    print(f"[{datetime.now()}] 큐: {queue_size}개 작업")

    # 워커 실행 중인지 확인
    if is_worker_running():
        print(f"[{datetime.now()}] 워커 실행 중 - 대기")
        return 0

    # 마지막 실행 후 최소 시간 체크
    status = get_status()
    if status.get("last_run"):
        try:
            last_run = datetime.fromisoformat(status["last_run"])
            elapsed = (datetime.now(timezone.utc) - last_run).total_seconds()
            if elapsed < MIN_RUN_INTERVAL_SEC:
                print(f"[{datetime.now()}] 마지막 실행 후 {elapsed:.0f}초 (대기)")
                return 0
        except (ValueError, TypeError):
            pass

    # 큐가 있고 워커가 안 돌고 있으면 실행
    if queue_size > 0:
        print(f"[{datetime.now()}] 워커 실행: {queue_size}개 작업")
        run_worker()
    else:
        print(f"[{datetime.now()}] 큐 비어 있음 - 대기")

    return 0


if __name__ == "__main__":
    import requests  # check_flaresolverr_health에서 사용
    sys.exit(main())