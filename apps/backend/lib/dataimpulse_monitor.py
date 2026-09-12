#!/usr/bin/env python3
"""DataImpulse 대시보드 모니터 - 프록시 기반 (Playwright)

10화마다 호출하여 DataImpulse 대시보드 사용량과 추적 데이터를 비교합니다.
두 가지 인증 모드 지원:
  1. 프록시 인증 (user:pass@gw.dataimpulse.com:823) — 현재 사용 중
  2. IP 화이트리스트 — 서버 IP를 DataImpulse 대시보드에 등록하면 인증 불요청

데이터 흐름:
  프록시(인증 또는 화이트리스트) → DataImpulse 대시보드 → 사용량 파싱 → 비교 로깅
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

_env_path = Path(__file__).parent.parent / ".env.local"


def _load_proxy_credentials() -> tuple[str, str, str, int]:
    """DataImpulse 프록시 인증 정보 로드. env vars가 .env.local보다 우선."""
    # 1. .env.local에서 기본값 로드
    file_user = ""
    file_pass = ""
    file_host = ""
    file_port = 823
    if _env_path.exists():
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATAIMPULSE_USER="):
                file_user = line.split("=", 1)[1].strip()
            elif line.startswith("DATAIMPULSE_PASS="):
                file_pass = line.split("=", 1)[1].strip()
            elif line.startswith("DATAIMPULSE_HOST="):
                file_host = line.split("=", 1)[1].strip()
            elif line.startswith("DATAIMPULSE_PORT="):
                try:
                    file_port = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass

    # 2. env vars가 .env.local보다 우선 (override)
    user = os.getenv("DATAIMPULSE_USER", file_user)
    passwd = os.getenv("DATAIMPULSE_PASS", file_pass)
    host = os.getenv("DATAIMPULSE_HOST", file_host or "gw.dataimpulse.com")
    port = int(os.getenv("DATAIMPULSE_PORT", str(file_port)))

    return user, passwd, host, port


def _proxy_url() -> str:
    """프록시 URL 구성 (credentials 있음/없음 둘 다 지원)."""
    user, passwd, host, port = _load_proxy_credentials()
    if user and passwd:
        return f"http://{user}:{passwd}@{host}:{port}"
    return f"http://{host}:{port}"


def _is_ip_whitelist_mode() -> bool:
    """IP 화이트리스트 모드 활성화 여부 확인."""
    return os.getenv("DATAIMPULSE_IP_WHITELIST", "").lower() in ("1", "true", "yes")


def _build_proxy_config() -> tuple[Optional[dict], str]:
    """프록시 설정과 모드 설명을 반환."""
    user, passwd, host, port = _load_proxy_credentials()
    whitelist_mode = _is_ip_whitelist_mode()

    if whitelist_mode:
        proxy_config = {"server": f"http://{host}:{port}"}
        mode_desc = f"IP Whitelist 모드 (프록시: {host}:{port})"
    else:
        proxy_config = {
            "server": _proxy_url(),
            "username": user,
            "password": passwd,
        }
        mode_desc = f"프록시 인증 모드 (프록시: {host}:{port})"

    return proxy_config, mode_desc


async def _access_dashboard(page, proxy_config: Optional[dict]):
    """프록시를 통해 대시보드에 접속하고 페이지 정보를 반환."""
    try:
        await page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
    except Exception:
        await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)

    await page.wait_for_timeout(5000)
    page_text = await page.content()
    current_url = page.url
    return page_text, current_url


def _parse_usage(page_text: str) -> tuple[Optional[float], Optional[float]]:
    """페이지 텍스트에서 사용량(GB)과 잔여량(GB)을 파싱."""
    used_gb = None
    remaining_gb = None

    match = re.search(r'\(?\s*([0-9.]+)\s*GB\s*\)?\s*(?:left|used)', page_text, re.IGNORECASE)
    if match:
        idx = match.start()
        if 'left' in page_text.lower()[idx:]:
            remaining_gb = float(match.group(1))
        else:
            used_gb = float(match.group(1))

    # GB 숫자만 있는 경우 (left/used 단어 없음) → used_gb로 기본 설정
    if used_gb is None and remaining_gb is None:
        matches = re.findall(r'([0-9.]+)\s*GB', page_text, re.IGNORECASE)
        if matches:
            used_gb = float(matches[-1])

    return used_gb, remaining_gb


async def check_dataimpulse_usage(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """DataImpulse 대시보드에서 사용량을 확인합니다 (프록시 기반).

    모드:
      1. IP 화이트리스트 모드 (DATAIMPULSE_IP_WHITELIST=1):
         프록시 인증 없이 서버 IP로 접근 (DataImpulse 대시보드에서 IP 등록 필요)
      2. 프록시 인증 모드 (기본):
         프록시 URL에 user:pass 포함하여 접근

    Returns:
        dict: success, used_gb, remaining_gb, tracked_used_mb, tracked_chapters, message
    """
    user, passwd, host, port = _load_proxy_credentials()
    whitelist_mode = _is_ip_whitelist_mode()

    if not whitelist_mode and (not user or not passwd):
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
            proxy_config, mode_desc = _build_proxy_config()
            logger.info("DataImpulse 대시보드 접속 중 — %s...", mode_desc)

            browser = await p.chromium.launch(headless=True, proxy=proxy_config)
            context = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ))
            page = await context.new_page()

            try:
                page_text, current_url = await _access_dashboard(page, proxy_config)

                if "sign-in" in current_url or "login" in current_url.lower():
                    result["message"] = (
                        "로그인 필요 — "
                        + ("IP 화이트리스트를 확인하세요" if whitelist_mode else "프록시 인증 확인 필요")
                    )
                    logger.warning("DataImpulse 대시보드: 로그인 페이지로 리다이렉트됨 (%s)", mode_desc)
                    _log_fallback_status(result)
                    return result

                used_gb, remaining_gb = _parse_usage(page_text)

                if used_gb is not None or remaining_gb is not None:
                    result["success"] = True
                    result["used_gb"] = used_gb
                    result["remaining_gb"] = remaining_gb
                    result["message"] = f"대시보드 확인 완료 ({mode_desc})"
                    _log_comparison(result)
                else:
                    result["message"] = "사용량 데이터 파싱 실패"
                    _log_fallback_status(result)

            finally:
                await browser.close()

            return result

    except Exception as e:
        result["message"] = f"대시보드 확인 실패: {e}"
        logger.warning(f"DataImpulse 대시보드 확인 실패: {e}")
        _log_fallback_status(result)
        return result


def _log_fallback_status(result: dict):
    """접근 실패 시 fallback 상태 로깅."""
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