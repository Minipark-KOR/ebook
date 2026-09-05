#!/usr/bin/env python3
"""북토끼 상태 체크 + 대안 소스 fallback 검증.

ebook-watcher의 health check 단계에서 주기적으로 실행.
북토끼 다운 감지 시 조아라/문피아/단편소설집 등 대안 시도.
"""
import sys
import time
import logging
from pathlib import Path

import requests

sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')
from services import bookto31

log = logging.getLogger("bookto31_healthcheck")

PRIMARY = "https://bookto31.com/"
BACKUP_SOURCES = [
    "https://www.joara.com/",
    "https://www.munpia.com/",
    # 필요시 추가
]


def check_url(url, timeout=10):
    """단순 HTTP GET으로 사이트 응답 확인."""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        })
        return resp.status_code == 200
    except Exception as e:
        log.warning(f"  {url}: {e}")
        return False


def main():
    log.info("북토끼 health check...")
    if check_url(PRIMARY):
        log.info(f"  ✓ 북토끼 정상")
        return "primary"

    log.warning(f"  ✗ 북토끼 다운! 대안 소스 시도...")
    for url in BACKUP_SOURCES:
        if check_url(url):
            log.info(f"  ✓ 대안 동작: {url}")
            return f"backup:{url}"

    log.error(f"  ✗ 모든 소스 다운!")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = main()
    if not result:
        sys.exit(1)
