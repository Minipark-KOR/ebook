#!/usr/bin/env python3
"""소스 레지스트리 — 다중 수집 소스 설정 관리.

sources.json 하나로 소스 추가/도메인 변경/수집기 지정을 코드 수정 없이 처리한다.
(북토끼/뉴토끼 등 사이트 주소가 자주 바뀌는 환경 대응)

사용:
    from lib.sources import get_base_url, get_source_from_url, get_collector

sources.json 형식:
    {
      "bookto31": {
        "domains": ["23.ondobook.net", "bookto31.com"],  # URL 매칭용 도메인 목록
        "base_url": "https://23.ondobook.net", # 크롤링 베이스 URL
        "collector": "bookto31",           # COLLECTORS 등록 키
        "discover": "gnuboard",            # discover 전략
        "speed_hint_sec": 300              # ETA fallback 속도
      }
    }
"""

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SOURCES_FILE = Path(__file__).resolve().parent.parent / "sources.json"

# 소스가 없을 때 기본값 (sources.json 실패 시에도 동작 보장)
_DEFAULT_SOURCES = {
    "bookto31": {
        "domains": ["23.ondobook.net", "bookto31.com"],
        "base_url": "https://23.ondobook.net",
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
    "newto31": {
        "domains": ["newto31.com"],
        "base_url": "https://newto31.com",
        "collector": "bookto31",
        "discover": "gnuboard",
        "speed_hint_sec": 300,
        "bo_tables": {
            "novel": "novel",
            "fafa19": "webtoon",
        },
    },
}


class SourceConfig(BaseModel):
    """단일 소스 설정 스키마 (pydantic 검증)."""

    domains: list[str] = Field(min_length=1)
    base_url: str
    collector: str
    discover: str = "unknown"
    speed_hint_sec: int = Field(default=300, ge=1, le=86400)
    # 적응형 딜레이 상/하한 (초) — 서버 응답시간 × 10 기반으로 이 구간 안에서 동적 조정.
    # 하한이 없으면 서버가 매우 빨라도 딜레이가 0에 가까워져 부하/차단 위험.
    # 상한은 속도 상한(hint) 역할. 기본: 하한 5s, 상한 speed_hint_sec.
    delay_min_sec: int = Field(default=5, ge=1, le=86400)
    delay_max_sec: int = Field(default=300, ge=1, le=86400)
    # 프록시(유료 트래픽) 사용 여부 — True면 트래픽 가드(일일 한도) 적용.
    # bookto31은 FlareSolverr 로컬(무료), toki31은 DataImpulse/MaskProxy(유료).
    traffic_limited: bool = False
    # bo_table(사이트 게시판) → media_type 매핑. "novel" 기본, 미정의 시 novel.
    # 예: newto31: {"fafa19": "webtoon", "novel": "novel"}
    bo_tables: dict[str, str] = Field(default_factory=dict)


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


def update_base_url(source: str, new_base_url: str) -> bool:
    """소스의 base_url을 sources.json에 영속화 (백업 + 원자적 쓰기).

    사이트 도메인 변경(리다이렉트 감지/페일오버) 시 자동 갱신용.
    신규 호스트는 domains 맨 앞에도 추가해 후보/매칭에 포함시킨다.
    실패(소스 없음/형식 오류) 시 False, 무변경/성공 시 True.
    """
    new_base_url = (new_base_url or "").rstrip("/")
    try:
        host = (urlparse(new_base_url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False

    raw: dict = {}
    if _SOURCES_FILE.exists():
        try:
            raw = json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}
    if source not in raw:
        logger.warning("update_base_url: 소스 %s 없음 — 갱신 생략", source)
        return False

    old = raw[source].get("base_url")
    if old == new_base_url:
        return True

    raw[source]["base_url"] = new_base_url
    domains = raw[source].get("domains", [])
    if host not in domains:
        raw[source]["domains"] = [host] + list(domains)

    try:
        if _SOURCES_FILE.exists():
            _SOURCES_FILE.rename(_SOURCES_FILE.with_suffix(".json.bak"))
        _SOURCES_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("sources.json 갱신: [%s] base_url %s → %s", source, old, new_base_url)
        return True
    except Exception as e:
        logger.warning("sources.json 쓰기 실패 (%s): %s", source, e)
        return False


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


def get_delay_bounds(source: str) -> tuple[int, int]:
    """적응형 딜레이 상/하한 (min, max) — 서버 응답시간×10을 이 구간에 클램프.

    bookto31(FlareSolverr): 30~300s (서버가 빠르면 30s, 느리면 300s)
    toki31(IP 회전): 5~30s
    """
    cfg = load_sources().get(source)
    if cfg:
        return cfg.delay_min_sec, cfg.delay_max_sec
    return 5, 300


def get_traffic_limited(source: str) -> bool:
    """프록시(유료 트래픽) 사용 여부 — 트래픽 가드 적용 대상인지.

    toki31(DataImpulse/MaskProxy)만 True. bookto31(FlareSolverr 로컬)은 무료.
    """
    cfg = load_sources().get(source)
    return bool(cfg.traffic_limited) if cfg else False


def get_media_type(source: str, bo_table: Optional[str]) -> str:
    """bo_table(사이트 게시판) → media_type 판별.

    sources.json의 bo_tables 매핑을 사용하고, 미정의/누락 시 기본 "novel".
    """
    mt = (bo_table or "").strip().lower()
    if not mt:
        return "novel"
    cfg = load_sources().get(source)
    if cfg and cfg.bo_tables:
        mapped = cfg.bo_tables.get(mt)
        if mapped:
            return mapped
    return "novel"


def list_sources() -> list[str]:
    return list(load_sources().keys())