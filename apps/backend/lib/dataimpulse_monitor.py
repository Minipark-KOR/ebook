#!/usr/bin/env python3
"""DataImpulse 대시보드 모니터 - CDP 기반 (Playwright)

10화마다 호출하여 DataImpulse 대시보드 사용량과 추적 데이터를 비교합니다.
Turnstile 인증이 필요한 경우 Gracefully fallback합니다.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

from lib.traffic_guard import summary as get_traffic_summary

logger = logging.getLogger(__name__)

DASHBOARD_URL = "https://app.dataimpulse.com/dashboard"
SIGNIN_URL = "https://app.dataimpulse.com/sign-in"

# .env.local에서 크레덴셜 로드
_env_path = Path(__file__).parent.parent / ".env.local"


def _load_credentials() -> tuple[str, str]:
    """DataImpulse 로그인/비밀번호 로드."""
    user = os.getenv("DATAIMPULSE_USER", "")
    passwd = os.getenv("DATAIMPULSE_PASS", "")
    if user and passwd:
        return user, passwd

    # .env.local에서 직접 파싱
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATAIMPULSE_USER="):
                user = line.split("=", 1)[1].strip()
            elif line.startswith("DATAIMPULSE_PASS="):
                passwd = line.split("=", 1)[1].strip()
    return user, passwd


async def check_dataimpulse_usage(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """DataImpulse 대시보드에서 사용량을 확인합니다 (CDP 직접 사용).

    Turnstile이 차단되면 Gracefully fallback하여 로그만 남깁니다.
    """
    user, passwd = _load_credentials()
    if not user or not passwd:
        msg = "DataImpulse 크레덴셜 없음 — 대시보드 확인 스킵"
        logger.warning(msg)
        return {"success": False, "used_gb": None, "remaining_gb": None, "message": msg}

    result = {
        "success": False,
        "used_gb": None,
        "remaining_gb": None,
        "tracked_used_mb": 0,
        "tracked_chapters": 0,
        "message": "",
    }

    try:
        async with async_playwright() as p:
            # CDP 연결 (기존 Playwright Chromium 등)
            browser = await p.chromium.connect_over_cdp(cdp_url)
            page = await browser.new_page()

            # 1. 대시보드 페이지로 이동 (networkidle 대기)
            logger.info("DataImpulse 대시보드 접속 중...")
            try:
                await page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
            except Exception:
                # networkidle 실패 시 domcontentloaded로 폴백
                await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)

            # 페이지 로드 대기
            await page.wait_for_timeout(5000)

            # 페이지 내용 확인
            page_text = await page.content()
            current_url = page.url

            # 2. 로그인 페이지로 리다이렉트되었으면 인증 필요
            if "sign-in" in current_url or "login" in current_url.lower():
                result["message"] = "로그인 필요 — Turnstile 인증 필요"
                logger.warning("DataImpulse 대시보드: 로그인 페이지로 리다이렉트됨 (Turnstile 인증 필요)")
                await browser.close()
                _log_fallback_status(result)
                return result

            # 3. 사용량 파싱 시도
            used_gb = None
            remaining_gb = None

            # 패턴 1: "X.XX GB left" 또는 "Y.YY GB used"
            match = re.search(r'\(?\s*([0-9.]+)\s*GB\s*\)?\s*(?:left|used)', page_text, re.IGNORECASE)
            if match:
                if 'left' in page_text.lower()[match.start():match.end()]:
                    remaining_gb = float(match.group(1))
                else:
                    used_gb = float(match.group(1))

            # 패턴 2: "X.XX GB" 숫자 패턴 (전체 페이지에서)
            if used_gb is None:
                matches = re.findall(r'([0-9.]+)\s*GB', page_text, re.IGNORECASE)
                if matches:
                    # 가장 최근/가장 큰 숫자나, 문맥에 맞는 것 선택
                    used_gb = float(matches[-1])  # 간단한 heuristic

            # 3. 비교 결과 구성
            if used_gb is not None or remaining_gb is not None:
                result["success"] = True
                result["used_gb"] = used_gb
                result["remaining_gb"] = remaining_gb
                result["message"] = "대시보드 확인 완료"

            # 4. 비교 로깅
            _log_comparison(result)

            await browser.close()
            return result

    except Exception as e:
        result["message"] = f"대시보드 확인 실패: {e}"
        logger.warning(f"DataImpulse 대시보드 확인 실패: {e}")
        _log_fallback_status(result)
        return result


def _log_fallback_status(result: dict):
    """Turnstile 차단 시 fallback 상태 로깅."""
    tracked = get_traffic_summary()
    logger.info(
        f"📊 DataImpulse fallback: {result.get('message', '알 수 없음')} | "
        f"추적 데이터: {tracked['used_mb']:.1f}MB / {tracked['chapters']}화"
    )


def _log_comparison(result: dict):
    """추적 데이터와 대시보드 데이터 비교 로깅."""
    tracked = get_traffic_summary()
    used_gb = result.get("used_gb")
    remaining_gb = result.get("remaining_gb")

    if used_gb is not None:
        dashboard_mb = used_gb * 1024
        diff_mb = dashboard_mb - tracked["used_mb"]
        diff_pct = (diff_mb / dashboard_mb * 100) if dashboard_mb > 0 else 0

        logger.info(
            f"📊 DataImpulse 비교: "
            f"추적={tracked['used_mb']:.1f}MB | "
            f"대시보드={used_gb:.2f}GB ({dashboard_mb:.1f}MB) | "
            f"차이={diff_mb:+.1f}MB ({diff_pct:+.1f}%)"
        )

        if abs(diff_pct) > 50:
            logger.warning(
                f"⚠️ 추적과 대시보드 차이가 큼 ({diff_pct:+.1f}%) — "
                f"불필요한 트래픽이 있을 수 있음"
            )
    elif remaining_gb is not None:
        logger.info(
            f"📊 DataImpulse 잔여: {remaining_gb:.2f}GB | "
            f"추적: {tracked['used_mb']:.1f}MB"
        )
    else:
        logger.info(f"📊 DataImpulse: {result.get('message', '알 수 없음')}")


def check_dataimpulse_sync(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """동기 버전 — 파이프라인에서 직접 호출."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, check_dataimpulse_usage(cdp_url))
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(check_dataimpulse_usage(cdp_url))
    except Exception as e:
        logger.warning(f"DataImpulse 확인 실패: {e}")
        return {"success": False, "message": str(e)}