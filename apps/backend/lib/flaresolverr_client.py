#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/flaresolverr_client.py
"""FlareSolverr 클라이언트 — Cloudflare Turnstile 우회 공유 세션 관리.

bookto31.py의 _session_*, _flaresolverr_*, _fetch_with_flaresolverr를 통합.
metadata_namu.py도 이 모듈을 사용하여 bookto31 import를 제거.
"""

import threading
import time
from typing import Optional, Dict

import requests

from lib.rate_limiter import wait_if_needed, record_request


FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
DEFAULT_TIMEOUT_MS = 60000
DEFAULT_INTERVAL = 480  # 8분
DEFAULT_JITTER = 120  # ±2분


class FlareSolverrSession:
    """FlareSolverr HTTP API 세션 관리.

    - 세션 생성/유지/파기
    - 쿠키/UA 캐싱 (cf_clearance 재사용)
    - rate limiter 연동 (선택)
    """

    def __init__(
        self,
        rate_limit: bool = True,
        interval: int = DEFAULT_INTERVAL,
        jitter: int = DEFAULT_JITTER,
        db_path: Optional[str] = None,
    ):
        self._session_id: Optional[str] = None
        self._cookies: Dict[str, str] = {}
        self._ua: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
        self._lock = threading.Lock()
        self._rate_limit = rate_limit
        self._interval = interval
        self._jitter = jitter
        self._db_path = db_path

    def fetch(self, url: str, max_attempts: int = 3) -> Optional[str]:
        """URL을 FlareSolverr로 요청, HTML 본문 반환. None이면 실패."""
        if self._rate_limit:
            wait_if_needed(
                url,
                interval=self._interval,
                db_path=self._db_path,
            )

        html = None
        for attempt in range(max_attempts):
            try:
                sol = self._flaresolverr_request(url)
            except Exception:
                time.sleep(1)
                continue

            if sol.get("status") == 200:
                self._update_session_state(sol)
                if self._rate_limit:
                    record_request(url, status=200, db_path=self._db_path)
                html = sol.get("response") or ""
                break
            time.sleep(2)

        if html is None and self._rate_limit:
            record_request(url, status=403, db_path=self._db_path)

        return html

    def _flaresolverr_request(self, url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Dict:
        """FlareSolverr로 URL 요청 → solution dict 반환."""
        payload = {"cmd": "request.get", "url": url, "maxTimeout": timeout_ms}
        with self._lock:
            if self._session_id:
                payload["session"] = self._session_id
        resp = requests.post(
            FLARESOLVERR_URL,
            json=payload,
            timeout=(timeout_ms / 1000) + 30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("solution") or {}

    def _update_session_state(self, sol: Dict) -> bool:
        """FlareSolverr 응답에서 쿠키/UA 추출 후 캐시 갱신. 성공 시 True."""
        if sol.get("status") != 200:
            return False
        cookies = sol.get("cookies") or []
        ua = sol.get("userAgent") or self._ua
        new_cookies: Dict[str, str] = {}
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            if name and value:
                new_cookies[name] = value
        if not new_cookies:
            return False
        with self._lock:
            self._cookies.clear()
            self._cookies.update(new_cookies)
            self._ua = ua
        return True

    def get_cookies(self) -> Dict[str, str]:
        """현재 캐시된 쿠키 반환."""
        with self._lock:
            return dict(self._cookies)

    def get_ua(self) -> str:
        """현재 UA 문자열 반환."""
        with self._lock:
            return self._ua

    def create_session(self) -> str:
        """FlareSolverr 세션 생성."""
        payload = {"cmd": "sessions.create"}
        resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        session_id = data.get("session") or ""
        with self._lock:
            self._session_id = session_id
        return session_id

    def destroy_session(self) -> None:
        """FlareSolverr 세션 파기."""
        with self._lock:
            sid = self._session_id
        if sid:
            try:
                requests.post(
                    FLARESOLVERR_URL,
                    json={"cmd": "sessions.destroy", "session": sid},
                    timeout=10,
                )
            except Exception:
                pass
            with self._lock:
                self._session_id = None
