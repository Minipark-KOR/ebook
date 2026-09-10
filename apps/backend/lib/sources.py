#!/usr/bin/env python3
"""소스 레지스트리 — 다중 수집 소스 설정 관리.

sources.json 하나로 소스 추가/도메인 변경/수집기 지정을 코드 수정 없이 처리한다.
(북토끼/뉴토끼 등 사이트 주소가 자주 바뀌는 환경 대응)

사용:
    from lib.sources import get_base_url, get_source_from_url, get_collector

sources.json 형식:
    {
      "bookto31": {
        "domains": ["bookto31.com"],      # URL 매칭용 도메인 목록
        "base_url": "https://bookto31.com", # 크롤링 베이스 URL
        "collector": "bookto31",           # COLLECTORS 등록 키
        "discover": "gnuboard",            # discover 전략
        "speed_hint_sec": 300              # ETA fallback 속도
      }
    }
"""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

_SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.json"

# 소스가 없을 때 기본값 (sources.json 실패 시에도 동작 보장)
_DEFAULT_SOURCES = {
    "bookto31": {
        "domains": ["bookto31.com"],
        "base_url": "https://bookto31.com",
        "collector": "bookto31",
        "discover": "gnuboard",
        "speed_hint_sec": 300,
    },
    "toki31": {
        "domains": ["toki31.com", "newtoki31.com"],
        "base_url": "https://toki31.com",
        "collector": "toki31",
        "discover": "toki31_episodes",
        "speed_hint_sec": 30,
    },
}


class SourceConfig(BaseModel):
    """단일 소스 설정 스키마 (pydantic 검증)."""

    domains: list[str] = Field(min_length=1)
    base_url: str
    collector: str
    discover: str = "unknown"
    speed_hint_sec: int = Field(default=300, ge=1, le=86400)
    # 프록시(유료 트래픽) 사용 여부 — True면 트래픽 가드(일일 한도) 적용.
    # bookto31은 FlareSolverr 로컬(무료), toki31은 DataImpulse/MaskProxy(유료).
    traffic_limited: bool = False


class SourcesConfig(BaseModel):
    """전체 소스 레지스트리. 키 → SourceConfig."""

    sources: dict[str, SourceConfig]


def load_sources() -> dict[str, SourceConfig]:
    """sources.json 로드 + pydantic 검증. 실패/파일 없으면 기본값."""
    if _SOURCES_FILE.exists():
        try:
            raw = json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
            cfg = SourcesConfig(sources=raw)
            return cfg.sources
        except Exception:
            pass  # 검증 실패 시 기본값 폴백
    return {k: SourceConfig(**v) for k, v in _DEFAULT_SOURCES.items()}


def get_source_from_url(url: str) -> Optional[str]:
    """URL 호스트로 소스 키 찾기. (URL_PATTERNS 대체)"""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    for key, cfg in load_sources().items():
        for domain in cfg.domains:
            if domain.lower() in host:
                return key
    return None


def get_base_url(source: str) -> str:
    """소스의 크롤링 베이스 URL."""
    cfg = load_sources().get(source)
    if cfg:
        return cfg.base_url
    return f"https://{source}.com"


def get_domains(source: str) -> list[str]:
    cfg = load_sources().get(source)
    return list(cfg.domains) if cfg else []


def get_collector(source: str) -> str:
    """소스의 수집기 등록 키 (COLLECTORS dict)."""
    cfg = load_sources().get(source)
    return cfg.collector if cfg else source


def get_discover(source: str) -> str:
    """소스의 discover 전략 (gnuboard | toki31_episodes | ...)."""
    cfg = load_sources().get(source)
    return cfg.discover if cfg else "unknown"


def get_speed_hint(source: str) -> int:
    """소스의 ETA fallback 속도(초/화)."""
    cfg = load_sources().get(source)
    return cfg.speed_hint_sec if cfg else 300


def get_traffic_limited(source: str) -> bool:
    """프록시(유료 트래픽) 사용 여부 — 트래픽 가드 적용 대상인지.

    toki31(DataImpulse/MaskProxy)만 True. bookto31(FlareSolverr 로컬)은 무료.
    """
    cfg = load_sources().get(source)
    return bool(cfg.traffic_limited) if cfg else False


def list_sources() -> list[str]:
    return list(load_sources().keys())