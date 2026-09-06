# toki31 본문 수집 — 한국 주거용 프록시 구현 계획

> **작성일**: 2026-09-06
> **갱신일**: 2026-09-06 (실측 검증 결과 반영)
> **목적**: toki31.com 본문 수집을 위한 한국 주거용 프록시 설정 및 구현
> **프록시**: DataImpulse ($1/GB) + MaskProxy ($0.87/GB)

## 1. 배경

### 1.1 문제점

- toki31은 일부 엔드포인트에 대해 **Cloudflare Access denied (403)** 적용 중
- Oracle Cloud datacenter IP에서는 `/novel/{id}` 상세/본문, `/rank`, `/search` 접근 차단
- curl_cffi chrome131 impersonation으로 TLS fingerprint 우회 가능하나, 차단의 정확한 원인(국가 필터 vs 봇 감지)은 아직 진단 필요
- 2026-09-06 실측: 403 응답 본문이 `Access denied | Cloudflare`로 확인 → **CloudFront geo-block 여부는 미확정, 추가 진단 필요**

### 1.2 실측 검증 결과 (2026-09-06, curl_cffi chrome131)

| 항목 | 상태 | 비고 |
|------|------|------|
| curl_cffi chrome131 | ✅ | TLS fingerprint 위장 성공 |
| `/` (루트) | ✅ | HTTP 200, 278KB |
| `/ing` (연재중) | ✅ | HTTP 200, 220KB |
| `/end` (완결) | ✅ | HTTP 200, 208KB |
| `/novel` (소설 목록) | ✅ | HTTP 200, 215KB — **RSC 31개, 소설 ID 42개 추출됨** |
| `/rank` (랭킹) | ❌ | HTTP 403 Cloudflare 차단 |
| `/novel/{id}` (상세) | ❌ | HTTP 403 Cloudflare 차단 |
| `/search` (검색) | ❌ | HTTP 403 Cloudflare 차단 |
| 무료 KR proxy | ❌ | 타임아웃, 안정성 없음 (기존 확인) |

> ⚠️ **기존 문서와의 차이**: `/novel` 목록 페이지는 **차단되지 않음** (HTTP 200, 소설 ID 추출 가능).
> 차단 경계는 `/novel/{id}` 상세/본문부터 시작. `/rank`는 403으로 **문서 기존 주장과 다름**.

### 1.3 요구사항

- **대상**: toki31 본문 (회차) 수집만
- **사용량**: 월 100건 이하 (~50MB)
- **예산**: 월 $10 이하
- **프록시**: 한국 주거용 IP만 가능
- **만료**: 없어야 함 (종량제 선호)

## 2. 프록시 비교 분석

### 2.1 DataImpulse

| 항목 | 내용 |
|------|------|
| URL | https://dataimpulse.com |
| 레지덴셜 단가 | $1/GB |
| 한국 모바일 | $2/GB |
| 시작 상품 | $5/5GB (만료 없음) |
| 지원 프로토콜 | HTTP, HTTPS, SOCKS5 |
| 특징 | 종량제, 트래픽 만료 없음, 국가 타기팅 포함 |

### 2.2 MaskProxy

| 항목 | 내용 |
|------|------|
| URL | https://maskproxy.io |
| 순환 레지덴셜 | $0.87/GB |
| 데이터센터 | $0.35/GB |
| 정적 레지덴셜 | $1.6/IP |
| 지원 프로토콜 | HTTP, SOCKS5 |
| 특징 | 도시/ASN 타기팅, 스티키 세션 지원 |

### 2.3 조합 전략

```
Primary:   MaskProxy ($0.87/GB)  ← 더 저렴
Fallback:  DataImpulse ($1/GB)   ← 백업
```

- 50MB/월 기준: 두 서비스 모두 사실상 무료 수준
- 중복성 확보: 하나가 차단되면 다른 것으로 전환
- IP 풀 다양성: 서로 다른 ASN/통신사 IP 사용

## 3. 아키텍처

### 3.1 현재 구조

```
services/toki31.py
  └─ lib/curl_session.py (curl_cffi, proxy 미사용)
       └─ toki31.com
            ├─ /, /ing, /end, /novel → ✅ 접속 가능
            └─ /novel/{id}, /rank, /search → ❌ Cloudflare 403 차단
```

> ⚠️ 403 차단은 Cloudflare에서 발생. CloudFront geo-block인지 Cloudflare 봇 감지인지는 불명확.
> 프록시 도입 전에 **직접 진단 테스트**를 권장함. (1.3 참조)

### 3.2 변경 후 구조

```
services/toki31.py
  └─ lib/proxy_session.py (신규)
       ├─ MaskProxy (Primary)
       ├─ DataImpulse (Fallback)
       └─ lib/curl_session.py (curl_cffi)
            └─ toki31.com (한국 프록시 경유)
```

### 3.3 흐름

```
1. toki31.py가 lib/proxy_session.py에 요청
2. proxy_session이 MaskProxy 프록시로 curl_cffi 세션 생성
3. 요청 실패 시 DataImpulse로 자동 전환
4. 응답 반환
```

## 4. 구현 상세

### 4.1 신규 파일: `lib/proxy_session.py`

```python
"""한국 주거용 프록시 관리 — DataImpulse + MaskProxy.

Primary: MaskProxy ($0.87/GB)
Fallback: DataImpulse ($1/GB)

lib/curl_session.py.create_curl_session()을 래핑하여 프록시 설정만 추가.
(기존 curl_session이 이미 proxy 파라미터를 지원하므로 코드 중복 방지)

환경변수:
  MASKPROXY_USER: MaskProxy 사용자명
  MASKPROXY_PASS: MaskProxy 비밀번호
  MASKPROXY_HOST: MaskProxy 호스트 (기본: proxy.maskproxy.io)
  MASKPROXY_PORT: MaskProxy 포트 (기본: 10000)

  DATAIMPULSE_USER: DataImpulse 사용자명
  DATAIMPULSE_PASS: DataImpulse 비밀번호
  DATAIMPULSE_HOST: DataImpulse 호스트 (기본: proxy.dataimpulse.com)
  DATAIMPULSE_PORT: DataImpulse 포트 (기본: 10000)
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

from curl_cffi import requests as creq
from lib.curl_session import create_curl_session

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
        return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)


def get_maskproxy_config() -> ProxyConfig:
    """MaskProxy 설정 로드."""
    return ProxyConfig(
        name="maskproxy",
        host=os.getenv("MASKPROXY_HOST", "proxy.maskproxy.io"),
        port=int(os.getenv("MASKPROXY_PORT", "10000")),
        username=os.getenv("MASKPROXY_USER", ""),
        password=os.getenv("MASKPROXY_PASS", ""),
    )


def get_dataimpulse_config() -> ProxyConfig:
    """DataImpulse 설정 로드."""
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
    (curl_session이 이미 proxy 파라미터를 지원하므로 코드 중복 방지)

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
) -> tuple[creq.Session, Optional[ProxyConfig]]:
    """Fallback이 포함된 세션 생성.

    Returns:
        (session, active_proxy_config)
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
```

### 4.2 변경 파일: `services/toki31.py`

```python
# 변경 사항:
# 1. lib/proxy_session import 추가
# 2. _session 생성 시 프록시 적용
# 3. _get() 실패 시 fallback 로직 추가

from lib.proxy_session import (
    get_proxy_session_with_fallback,
    get_maskproxy_config,
    get_dataimpulse_config,
)

# 기존:
# _session = create_curl_session(impersonate="chrome131")

# 변경:
_session, _active_proxy = get_proxy_session_with_fallback()


def _get(url: str, timeout: int = 15) -> Optional[str]:
    """curl_cffi GET 요청. 실패 시 fallback 프록시로 재시도."""
    if _session is None:
        logger.error("프록시 미설정 - _get() 호출 불가")
        return None

    try:
        resp = _session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.text

        # 403 또는 프록시 관련 에러 시 fallback 시도
        if resp.status_code in (403, 407, 502, 503):
            return _get_with_fallback(url, timeout)

        return None
    except Exception:
        return _get_with_fallback(url, timeout)


def _get_with_fallback(url: str, timeout: int) -> Optional[str]:
    """Fallback 프록시로 재시도."""
    if _active_proxy is None:
        return None

    maskproxy = get_maskproxy_config()
    dataimpulse = get_dataimpulse_config()

    # 현재 프록시와 다른 것으로 시도
    fallback = dataimpulse if _active_proxy.name == "maskproxy" else maskproxy

    if fallback.is_configured:
        try:
            from lib.proxy_session import create_proxy_session
            session = create_proxy_session("chrome131", fallback)
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 200:
                logger.info(f"Fallback 성공: {fallback.name}")
                return resp.text
        except Exception:
            pass

    return None
```

### 4.3 환경변수 설정

```bash
# .env 또는 systemd 환경변수

# MaskProxy (Primary)
MASKPROXY_USER=your_maskproxy_user
MASKPROXY_PASS=your_maskproxy_pass
MASKPROXY_HOST=proxy.maskproxy.io
MASKPROXY_PORT=10000

# DataImpulse (Fallback)
DATAIMPULSE_USER=your_dataimpulse_user
DATAIMPULSE_PASS=your_dataimpulse_pass
DATAIMPULSE_HOST=proxy.dataimpulse.com
DATAIMPULSE_PORT=10000
```

## 5. 구현 단계

### Phase 1: 프록시 라이브러리 구축 (1일)

| 작업 | 파일 | 설명 |
|------|------|------|
| 1.1 | `lib/proxy_session.py` | 프록시 관리 모듈 신규 생성 |
| 1.2 | `.env` | 환경변수 추가 |
| 1.3 | 단위 테스트 | 프록시 연결 테스트 |

### Phase 2: toki31 통합 (1일)

| 작업 | 파일 | 설명 |
|------|------|------|
| 2.1 | `services/toki31.py` | proxy_session 사용으로 변경 |
| 2.2 | `_get()` | fallback 로직 추가 |
| 2.3 | 통합 테스트 | toki31 본문 수집 테스트 |

### Phase 3: 모니터링 (0.5일)

| 작업 | 파일 | 설명 |
|------|------|------|
| 3.1 | `lib/proxy_session.py` | 사용량 추적 로깅 |
| 3.2 | 테스트 | 장기 안정성 테스트 |

## 6. 테스트 계획

### 6.1 프록시 연결 테스트

```python
# scripts/test_proxy.py

def test_maskproxy_connection():
    """MaskProxy 연결 테스트."""
    config = get_maskproxy_config()
    session = create_proxy_session("chrome131", config)
    resp = session.get("https://httpbin.org/ip", timeout=10)
    assert resp.status_code == 200
    assert "origin" in resp.json()


def test_dataimpulse_connection():
    """DataImpulse 연결 테스트."""
    config = get_dataimpulse_config()
    session = create_proxy_session("chrome131", config)
    resp = session.get("https://httpbin.org/ip", timeout=10)
    assert resp.status_code == 200
    assert "origin" in resp.json()
```

### 6.2 toki31 본문 수집 테스트

```python
# scripts/test_toki31_proxy.py

def test_toki31_novel_list_with_proxy():
    """프록시를 통한 toki31 소설 목록 수집."""
    from services.toki31 import fetch_novel_list
    html = fetch_novel_list()
    assert html is not None
    assert len(html) > 1000


def test_toki31_chapter_with_proxy():
    """프록시를 통한 toki31 본문 수집."""
    from services.toki31 import fetch_chapter
    html = fetch_chapter(58455, 1)  # 예시 novel_id, chapter_id
    assert html is not None
    body = parse_chapter_body(html)
    assert len(body) > 100
```

## 7. 비용 추정

### 7.1 사용량

| 항목 | 값 |
|------|-----|
| 월 요청 수 | 100건 |
| 페이지당 크기 | ~0.5MB |
| 월 총 사용량 | ~50MB |

### 7.2 비용

| 서비스 | 월 비용 |
|--------|--------|
| MaskProxy | ~$0.04 (50MB × $0.87/GB) |
| DataImpulse | ~$0.05 (50MB × $1/GB) |
| **합계** | **~$0.09/월** |

> ⚠️ 실제 비용은 TLS 핸드셰이크/DNS 트래픽 포함 시 **약 1.5~2배** 예상 (총 ~$0.18/월)

### 7.3 시작 비용

| 서비스 | 시작 비용 |
|--------|----------|
| DataImpulse | $5 (5GB, 만료 없음) |
| MaskProxy | 무료 가입 |

## 8. 주의사항

### 8.1 프록시 한도

- 두 서비스 모두 무제한 동시 연결 지원
- 과도한 요청 시 일시적 차단 가능
- 권장: 초당 1건 이하로 제한

### 8.2 IP 로테이션

- MaskProxy: 요청별 IP 로테이션 기본
- DataImpulse: sticky 세션 필요 시 별도 설정

### 8.3 모니터링

- 프록시 응답 시간 모니터링
- 실패율 추적
- 사용량 로깅

## 9. 관련 문서

| 문서 | 설명 |
|------|------|
| [08-TOKI31-ANALYSIS.md](08-TOKI31-ANALYSIS.md) | toki31 보안 분석 |
| [02-BOT-BYPASS.md](02-BOT-BYPASS.md) | 봇 우회 전략 |
| [00-ARCHITECTURE.md](00-ARCHITECTURE.md) | 시스템 아키텍처 |

## 10. 구현 상태

### ✅ 완료 (2026-09-06)

| 단계 | 파일 | 상태 |
|------|------|------|
| Phase 1.1 | `lib/proxy_session.py` | ✅ 신규 생성 |
| Phase 1.2 | `.env` | ✅ 환경변수 추가 |
| Phase 1.3 | `scripts/test_proxy.py` | ✅ 테스트 스크립트 |
| Phase 2.1 | `services/toki31.py` | ✅ 프록시 적용 |
| Phase 2.2 | `services/toki31.py` | ✅ fallback 로직 |
| Phase 2.3 | 테스트 실행 | ✅ 통과 (프록시 미설정 시 스킵) |

### ⏳ 남은 단계

0. **프록시 계정 생성** (MaskProxy/DataImpulse)
1. **환경변수 설정** (`.env`에 프록시 키 입력)
2. **실제 연결 테스트** (프록시 설정 후 `python scripts/test_proxy.py`)
3. **프로덕션 배포**

### 다음 단계

1. MaskProxy 계정 생성: https://maskproxy.io
2. DataImpulse 계정 생성: https://dataimpulse.com ($5/5GB)
3. `.env` 파일에 프록시 키 입력
4. `python scripts/test_proxy.py`로 연결 테스트
5. toki31 본문 수집 테스트
