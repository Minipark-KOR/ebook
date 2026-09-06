#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/proxy_session.py
"""한국 주거용 프록시 관리 — DataImpulse + MaskProxy.

Primary: MaskProxy ($0.87/GB)
Fallback: DataImpulse ($1/GB)

lib/curl_session.py.create_curl_session()을 래핑하여 프록시 설정만 추가.
(curl_session이 이미 proxy 파라미터를 지원하므로 코드 중복 방지)

환경변수 (.env.local에서 로드):
  MASKPROXY_USER: MaskProxy 사용자명
  MASKPROXY_PASS: MaskProxy 비밀번호
  MASKPROXY_HOST: MaskProxy 호스트 (기본: proxy.maskproxy.net)
  MASKPROXY_PORT: MaskProxy 포트 (기본: 12324)

  DATAIMPULSE_USER: DataImpulse 사용자명
  DATAIMPULSE_PASS: DataImpulse 비밀번호
  DATAIMPULSE_HOST: DataImpulse 호스트 (기본: proxy.dataimpulse.com)
  DATAIMPULSE_PORT: DataImpulse 포트 (기본: 10000)
"""

import os
import logging
from typing import Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as creq

from lib.curl_session import create_curl_session

# .env.local 파일에서 환경변수 로드
_env_local = Path(__file__).parent.parent / ".env.local"
if _env_local.exists():
    for line in _env_local.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    """프록시 설정."""
    name: str
    host: str
    port: int
    username: str
    password: str
    protocol: str = "http"

    @property
    def url(self) -> str:
        """프록시 URL (형식: http://user:pass@host:port)."""
        return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"

    @property
    def is_configured(self) -> bool:
        """사용자명/비밀번호 설정 여부."""
        return bool(self.username and self.password)


def get_maskproxy_config() -> ProxyConfig:
    """MaskProxy 설정 로드 (환경변수)."""
    return ProxyConfig(
        name="maskproxy",
        host=os.getenv("MASKPROXY_HOST", "proxy.maskproxy.io"),
        port=int(os.getenv("MASKPROXY_PORT", "10000")),
        username=os.getenv("MASKPROXY_USER", ""),
        password=os.getenv("MASKPROXY_PASS", ""),
    )


def get_dataimpulse_config() -> ProxyConfig:
    """DataImpulse 설정 로드 (환경변수)."""
    return ProxyConfig(
        name="dataimpulse",
        host=os.getenv("DATAIMPULSE_HOST", "proxy.dataimpulse.com"),
        port=int(os.getenv("DATAIMPULSE_PORT", "10000")),
        username=os.getenv("DATAIMPULSE_USER", ""),
        password=os.getenv("DATAIMPULSE_PASS", ""),
    )


def create_proxy_session(
    impersonate: str = "chrome131",
    proxy_config: Optional[ProxyConfig] = None,
) -> creq.Session:
    """프록시가 적용된 curl_cffi 세션 생성.

    기존 lib/curl_session.create_curl_session()을 래핑하여 프록시만 추가.

    Args:
        impersonate: 브라우저 위장 타입
        proxy_config: 프록시 설정 (None이면 프록시 미사용)

    Returns:
        curl_cffi Session
    """
    proxy_url = proxy_config.url if (proxy_config and proxy_config.is_configured) else None
    session = create_curl_session(impersonate=impersonate, proxy=proxy_url)

    if proxy_config and proxy_config.is_configured:
        logger.info(f"프록시 적용: {proxy_config.name}")

    return session


def get_proxy_session_with_fallback(
    impersonate: str = "chrome131",
) -> Tuple[Optional[creq.Session], Optional[ProxyConfig]]:
    """Fallback이 포함된 세션 생성.

    Returns:
        (session, active_proxy_config) - 프록시 미설정 시 (None, None)
    """
    maskproxy = get_maskproxy_config()
    dataimpulse = get_dataimpulse_config()

    # Primary: MaskProxy
    if maskproxy.is_configured:
        session = create_proxy_session(impersonate, maskproxy)
        return session, maskproxy

    # Fallback: DataImpulse
    if dataimpulse.is_configured:
        session = create_proxy_session(impersonate, dataimpulse)
        return session, dataimpulse

    # 프록시 미설정 시 실패 반환 (Oracle Cloud → 직접 접속 무의미)
    logger.error("프록시 미설정 - toki31 본문 수집 불가 (Oracle Cloud IP 차단)")
    return None, None
