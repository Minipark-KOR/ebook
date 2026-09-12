#!/usr/bin/env python3
"""DataImpulse 대시보드 모니터 — 프록시 없이 직접 접속해 사용량 확인.

10화마다 호출하여 추적 데이터와 실제 DataImpulse 사용량을 비교한다.
프록시를 사용하지 않으므로 DataImpulse 트래픽에는 영향 없음.
"""

import asyncio
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DASHBOARD_URL = "https://app.dataimpulse.com"
SIGNIN_URL = f"{DASHBOARD_URL}/sign-in"

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


async def check_dataimpulse_usage() -> dict:
    """DataImpulse 대시보드에서 사용량을 확인한다.

    Returns:
        {
            "success": bool,
            "used_gb": float or None,
            "remaining_gb": float or None,
            "message": str,
        }
    """
    from lib.traffic_guard import current_bytes, summary

    user, passwd = _load_credentials()
    if not user or not passwd:
        msg = "DataImpulse 크레덴셜 없음 — 대시보드 확인 스킵"
        logger.warning(msg)
        return {"success": False, "used_gb": None, "remaining_gb": None, "message": msg}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        msg = "playwright 미설치 — 대시보드 확인 스킵"
        logger.warning(msg)
        return {"success": False, "used_gb": None, "remaining_gb": None, "message": msg}

    tracked = summary()
    result = {
        "success": False,
        "used_gb": None,
        "remaining_gb": None,
        "tracked_used_mb": tracked["used_mb"],
        "tracked_chapters": tracked["chapters"],
        "message": "",
    }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # 프록시 없이 직접 접속
            context = await browser.new_context()

            # 리소스 차단 — 대시보드 필요 최소한만 로드
            await context.route("**/*", _block_dashboard_resources)

            page = await context.new_page()

            # 1. 로그인
            logger.info("DataImpulse 대시보드 로그인 중...")
            await page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 이메일 입력
            email_input = page.locator('input[type="email"]').first
            await email_input.fill(user)

            # 비밀번호 입력
            pw_input = page.locator('input[type="password"]').first
            await pw_input.fill(passwd)

            # 로그인 버튼이 활성될 때까지 대기 후 클릭
            login_btn = page.locator('button:has-text("Log In")').first
            await login_btn.wait_for(state="visible", timeout=10000)
            # 버튼이 활성화될 때까지 대기 (Cloudflare Turnstile 처리 대기)
            for _ in range(30):
                is_disabled = await login_btn.get_attribute("disabled")
                if is_disabled is None:
                    break
                await page.wait_for_timeout(1000)
            await login_btn.click()

            # 대시보드 로드 대기
            await page.wait_for_timeout(5000)

            # 2. 대시보드에서 사용량 파싱
            page_text = await page.content()

            # 잔여 GB 파싱 (여러 패턴 시도)
            used_gb = None
            remaining_gb = None

            # 패턴 1: "X.XX GB / Y.YY GB" 또는 "Used: X.XX GB"
            match = re.search(r'Used[:\s]*([0-9.]+)\s*GB', page_text, re.IGNORECASE)
            if match:
                used_gb = float(match.group(1))

            # 패턴 2: "Remaining: X.XX GB"
            match = re.search(r'Remaining[:\s]*([0-9.]+)\s*GB', page_text, re.IGNORECASE)
            if match:
                remaining_gb = float(match.group(1))

            # 패턴 3: "X.XX GB left"
            match = re.search(r'([0-9.]+)\s*GB\s*left', page_text, re.IGNORECASE)
            if match and remaining_gb is None:
                remaining_gb = float(match.group(1))

            # 패턴 4: 잔여량이 있으면 사용량 계산
            if remaining_gb is not None and used_gb is None:
                # DataImpulse는 보통 잔여량을 표시
                # 사용량 = 전체 - 잔여량 (전체는 plans에 따라 다름)
                pass

            if used_gb is not None or remaining_gb is not None:
                result["success"] = True
                result["used_gb"] = used_gb
                result["remaining_gb"] = remaining_gb
                result["message"] = "대시보드 확인 완료"
            else:
                # 파싱 실패 — 페이지 스크린샷 저장 (디버깅용)
                screenshot_path = Path("/tmp/dataimpulse_dashboard.png")
                await page.screenshot(path=str(screenshot_path))
                result["message"] = f"사용량 파싱 실패 — 스크린샷 저장: {screenshot_path}"

            await browser.close()

    except Exception as e:
        result["message"] = f"대시보드 확인 실패: {e}"
        logger.warning(f"DataImpulse 대시보드 확인 실패: {e}")

    # 3. 비교 결과 로깅
    _log_comparison(result)
    return result


async def _block_dashboard_resources(route):
    """대시보드 리소스 차단 — 텍스트/API만 허용."""
    url = route.request.url
    resource_type = route.request.resource_type

    # 불필요한 리소스 차단
    if resource_type in ("image", "font", "media"):
        await route.abort()
        return

    # 불필요한 도메인 차단
    block_domains = ["google-analytics", "googletagmanager", "hotjar", "intercom"]
    if any(d in url for d in block_domains):
        await route.abort()
        return

    await route.continue_()


def _log_comparison(result: dict):
    """추적 데이터와 대시보드 데이터 비교 로깅."""
    tracked_mb = result.get("tracked_used_mb", 0)
    used_gb = result.get("used_gb")
    remaining_gb = result.get("remaining_gb")

    if used_gb is not None:
        dashboard_mb = used_gb * 1024
        diff_mb = dashboard_mb - tracked_mb
        diff_pct = (diff_mb / dashboard_mb * 100) if dashboard_mb > 0 else 0

        logger.info(
            f"📊 DataImpulse 비교: "
            f"추적={tracked_mb:.1f}MB | "
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
            f"추적: {tracked_mb:.1f}MB"
        )
    else:
        logger.info(f"📊 DataImpulse: {result.get('message', '알 수 없음')}")


def check_dataimpulse_sync() -> dict:
    """동기 버전 — 파이프라인에서 직접 호출."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 이미 이벤트 루프가 돌아가면 태스크로 실행
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, check_dataimpulse_usage())
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(check_dataimpulse_usage())
    except Exception as e:
        logger.warning(f"DataImpulse 확인 실패: {e}")
        return {"success": False, "message": str(e)}
