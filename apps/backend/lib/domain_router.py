#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/domain_router.py
"""도메인 라우팅 / 자동 전환 유틸리티.

북토끼(bookto31)/뉴토끼(toki31) 등 사이트가 도메인을 자주 바꾸는 환경 대응:

1. 리다이렉트 최종 URL 감지 → sources.json base_url 자동 갱신
   (구 도메인이 새 도메인으로 리다이렉트를 거는 동안 이를 활용)
2. 후보 도메인(domains 목록) 페일오버 — 현재 도메인 사망 시 다음 후보 시도
3. 헬스체크 — 현재 도메인 생존 여부 확인(실제 응답 or DNS), 사망 시 자동 전환

이벤트(도메인 변경/사망)는 domain_status.json에 기록되어
어드민/모니터링에서 확인할 수 있다.
"""

import datetime
import json
import logging
import socket
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urlparse

from lib.sources import get_base_url, load_sources, update_base_url

logger = logging.getLogger(__name__)

DOMAIN_STATUS_FILE = Path("/opt/ai_data/flaresolverr/domain_status.json")
_MAX_STATUS_RECORDS = 50


def host_of(url: str) -> str:
    """URL의 호스트명(소문자). 실패 시 빈 문자열."""
    try:
        return (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def base_of(url: str) -> str:
    """scheme://host 형태의 베이스 URL 반환. 실패 시 빈 문자열."""
    try:
        p = urlparse(url or "")
        scheme = (p.scheme or "https").lower()
        host = (p.hostname or "").lower()
        return f"{scheme}://{host}" if host else ""
    except Exception:
        return ""


def record_domain_event(event: dict) -> None:
    """도메인 변경/헬스 이벤트를 domain_status.json에 기록 (최근 N개 보관)."""
    try:
        evt = dict(event)
        evt.setdefault("ts", datetime.datetime.now(datetime.timezone.utc).isoformat())
        records: list = []
        if DOMAIN_STATUS_FILE.exists():
            try:
                loaded = json.loads(DOMAIN_STATUS_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    records = loaded
            except Exception:
                records = []
        records.append(evt)
        records = records[-_MAX_STATUS_RECORDS:]
        DOMAIN_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DOMAIN_STATUS_FILE.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def auto_update_base(
    source: str, requested_url: str, resolved_url: Optional[str]
) -> Optional[str]:
    """리다이렉트 최종 URL이 요청 URL과 다른 호스트면 base_url 자동 갱신.

    Args:
        source: 소스 키 (bookto31/toki31 ...)
        requested_url: 우리가 요청한 원래 URL
        resolved_url: 응답의 실제 최종 URL (리다이렉트 후)

    Returns:
        새로 갱신된 base_url 또는 None (변경 없음/실패)
    """
    if not resolved_url:
        return None
    req_host = host_of(requested_url)
    res_host = host_of(resolved_url)
    if not req_host or not res_host or req_host == res_host:
        return None
    new_base = base_of(resolved_url)
    if not new_base:
        return None
    if update_base_url(source, new_base):
        msg = f"🔄 [{source}] 도메인 자동 전환 (리다이렉트): {req_host} → {res_host} ({new_base})"
        logger.warning(msg)
        record_domain_event({
            "source": source,
            "event": "auto_redirect",
            "old_host": req_host,
            "new_host": res_host,
            "new_base_url": new_base,
        })
        return new_base
    return None


def candidate_bases(source: str) -> Iterator[str]:
    """현재 base_url 우선, 나머지 domains를 미러로 순회 (중복 제거).

    현재 base_url의 scheme을 미러 도메인에도 동일 적용.
    """
    cfg = load_sources().get(source)
    if not cfg:
        return
    current = (cfg.base_url or "").rstrip("/")
    scheme = (urlparse(current).scheme or "https").lower()
    seen = {host_of(current)} if current else set()
    if current:
        yield current
    for domain in cfg.domains:
        d = (domain or "").strip().lower().rstrip("/")
        if not d:
            continue
        if "://" in d:
            candidate = d
            cand_host = host_of(d)
        else:
            candidate = f"{scheme}://{d}"
            cand_host = d
        if not cand_host or cand_host in seen:
            continue
        seen.add(cand_host)
        yield candidate


def dns_alive(host: str, timeout: float = 3.0) -> bool:
    """호스트 DNS가 해석되는지 빠르게 확인 (네트워크 트래픽 비용 0)."""
    if not host:
        return False
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except Exception:
        return False


def check_domain_health(
    source: str,
    probe_fn: Optional[Callable[[str], Optional[bool]]] = None,
    notify: Optional[Callable[[dict, str], None]] = None,
) -> dict:
    """소스의 현재 도메인 생존 확인. 사망 시 후보 도메인으로 페일오버/자동 갱신.

    Args:
        source: 소스 키
        probe_fn: base_url → 생존 여부 콜백. True/False 반환, None이면 판별 불가
            (프로브 인프라 다운 등). None이면 DNS 체크만.
        notify: (event, message) 알림 콜백 (선택).

    Returns:
        {"source", "status": "ok"|"failover"|"down"|"unknown", "base_url", "detail"}
        - unknown: 프로브 인프라(FlareSolverr 등) 문제로 판별 불가 — 페일오버하지 않음.
    """
    current = get_base_url(source)

    def _probe(base: str) -> Optional[bool]:
        if probe_fn is None:
            return dns_alive(host_of(base))
        try:
            res = probe_fn(base)
        except Exception:
            return None
        if res is None:
            return None
        return bool(res)

    p = _probe(current)
    if p is True:
        return {"source": source, "status": "ok", "base_url": current}
    if p is None:
        # 프로브 자체가 불가(예: FlareSolverr 다운) → 사이트 판별 불가, 페일오버 금지
        logger.warning(
            "[%s] 도메인 생존 판별 불가 (프로브 인프라 문제) — base_url 유지",
            source,
        )
        return {"source": source, "status": "unknown", "base_url": current, "detail": "probe_unavailable"}

    # 현재 도메인 사망 → 후보 페일오버
    results = []
    for base in candidate_bases(source):
        if base == current:
            continue
        ok = _probe(base)
        results.append({"base_url": base, "alive": ok})
        if ok:
            update_base_url(source, base)
            evt = {
                "source": source,
                "event": "failover",
                "old_base_url": current,
                "new_base_url": base,
                "detail": "dns" if probe_fn is None else "probe",
            }
            msg = f"🔄 [{source}] 도메인 페일오버: {current} → {base}"
            logger.warning(msg)
            record_domain_event(evt)
            if notify:
                try:
                    notify(evt, msg)
                except Exception:
                    pass
            return {"source": source, "status": "failover", "base_url": base, "detail": evt}

    evt = {
        "source": source,
        "event": "down",
        "old_base_url": current,
        "detail": results,
    }
    msg = f"⚠️ [{source}] 현재·후보 도메인 모두 불가 — 수동 등록 필요 (base_url={current})"
    logger.error(msg)
    record_domain_event(evt)
    if notify:
        try:
            notify(evt, msg)
        except Exception:
            pass
    return {"source": source, "status": "down", "base_url": current, "detail": results}