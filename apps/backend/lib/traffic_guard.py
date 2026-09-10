#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/traffic_guard.py
"""일일 트래픽 한도 가드 — 프록시 사용량 실측 누적 + 일일 한도 제어.

DataImpulse/MaskProxy는 GB당 과금. 과소진 방지를 위해 실제 다운로드 바이트를
누적하고 일일 한도 초과 시 수집을 일시정지한다. (다음 날 자동 리셋)

- 일일 한도: 환경변수 EBOOK_DAILY_TRAFFIC_LIMIT_MB (기본 200MB/일)
- 상태 파일: /opt/ai_data/flaresolverr/ebook_watcher/traffic_state.json
  {"date": "2026-09-10", "bytes": 12345678, "chapters": 45}
- 날짜가 바뀌면 누적 바이트 자동 리셋 → loop가 자정 후 재개
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path('/opt/ai_data/flaresolverr/ebook_watcher/traffic_state.json')

DEFAULT_DAILY_LIMIT_MB = 200


def get_daily_limit_mb() -> int:
    try:
        return int(os.getenv('EBOOK_DAILY_TRAFFIC_LIMIT_MB', str(DEFAULT_DAILY_LIMIT_MB)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_LIMIT_MB


def daily_limit_bytes() -> int:
    return get_daily_limit_mb() * 1024 * 1024


def _today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {"date": _today(), "bytes": 0, "chapters": 0, "last_exceeded_at": None}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.warning(f"traffic state 저장 실패: {e}")


def reset_if_new_day() -> None:
    """날짜가 바뀌었으면 일일 누적 리셋."""
    state = load_state()
    if state.get('date') != _today():
        state = {"date": _today(), "bytes": 0, "chapters": 0, "last_exceeded_at": None}
        save_state(state)
        logger.info(f"traffic guard: 새 일자({_today()}) 리셋")


def current_bytes() -> int:
    return int(load_state().get('bytes', 0))


def add_bytes(n: int, chapter: bool = False) -> dict:
    """다운로드 바이트 누적. chapter=True면 회차 수도 증가."""
    state = load_state()
    state['bytes'] = int(state.get('bytes', 0)) + max(0, int(n))
    if chapter:
        state['chapters'] = int(state.get('chapters', 0)) + 1
    save_state(state)
    return state


def remaining_bytes() -> int:
    return max(0, daily_limit_bytes() - current_bytes())


def is_exceeded() -> bool:
    return current_bytes() >= daily_limit_bytes()


def seconds_until_next_day() -> int:
    """다음 자정(로컬)까지 남은 초. 최소 1초."""
    now = datetime.now()
    tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
    return max(1, int((tomorrow - now).total_seconds()))


def summary() -> dict:
    """현재 상태 요약 (로깅/상태 파일용)."""
    limit = daily_limit_bytes()
    used = current_bytes()
    return {
        "daily_limit_mb": get_daily_limit_mb(),
        "used_mb": round(used / (1024 * 1024), 2),
        "remaining_mb": round(max(0, limit - used) / (1024 * 1024), 2),
        "exceeded": used >= limit,
        "chapters": int(load_state().get('chapters', 0)),
    }