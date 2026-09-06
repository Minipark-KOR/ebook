# 봇 탐지 우회 전략

> Cloudflare, CloudFront, ASN 차단 등을 우회하는 방법.
>
> ⚠️ **toki31.com (뉴토끼) 보안 구조 변경됨**: 2026-09-06 재분석 결과, CloudFront → Cloudflare + Next.js RSC로 전환.
> 자세한 분석은 [08-TOKI31-ANALYSIS.md](08-TOKI31-ANALYSIS.md) 참조.

## 우회 방법 요약

| 대상 사이트 | 차단 유형 | 우회 방법 | 비용 |
|---|---|---|---|
| bookto31.com (북토끼) | Cloudflare Turnstile | FlareSolverr (헤드리스 브라우저) | 무료 (셀프호스팅) |
| toki31.com (뉴토끼) | CloudFront ASN + KR_ONLY | curl_cffi TLS 위장 + Residential proxy | 무료 (단, 불안정) |
| miniebook.vercel.app | Vercel 기본 보안 | 없음 (정상 API) | - |

## 1. 북토끼 우회 (FlareSolverr)

### 1.1 FlareSolverr란?
- 헤드리스 브라우저 (Playwright + Chromium)로 동작
- Cloudflare Turnstile Challenge를 풀어서 `cf_clearance` 쿠키 발급
- HTTP API로 결과 반환 (HTML, 쿠키, User-Agent)

### 1.2 배포 (Quadlet)
- 위치: `~/.config/containers/systemd/svc.pod`
- 이미지: `ghcr.io/flaresolverr/flaresolverr:latest` (ARM64 지원)
- 포트: `127.0.0.1:8191` (내부 접근만)
- 의존: `container-flaresolverr.service` (podman run --pod=svc)

### 1.3 svc.pod 충돌 해결
**문제**: svc.pod가 `PublishPort=80:80, 443:443` 포함 → caddy와 충돌로 시작 실패

**해결** (`/home/opc/.config/containers/systemd/svc.pod`):
```ini
[Pod]
PodName=svc
Network=devforge-net
PublishPort=127.0.0.1:8000:8000   # MCP
PublishPort=127.0.0.1:8191:8191   # FlareSolverr
ExitPolicy=continue
# 80, 443은 caddy가 담당
```

### 1.4 운영 명령
```bash
# 상태 확인
systemctl --user status svc-pod
podman pod ls

# 재시작
systemctl --user restart svc-pod container-flaresolverr

# 로그
journalctlctl --user -u container-flaresolverr.service -f

# 헬스체크
curl http://127.0.0.1:8191/health
```

### 1.5 bookto31.py 통합
```python
def _fetch_with_flaresolverr(url: str, rate_limit: bool = True) -> Optional[str]:
    if rate_limit:
        _rate_limit_check(url)  # 8분 + ±2분 jitter
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=90)
    data = resp.json()
    sol = data.get("solution") or {}
    if sol.get("status") == 200:
        _update_session_state(sol)  # cf_clearance 쿠키 캐싱
        return sol.get("response")
    return None
```

### 1.6 FlareSolverr 응답 형식
```json
{
  "status": "ok",
  "message": "Challenge solved!",
  "solution": {
    "status": 200,
    "url": "https://bookto31.com/...",
    "cookies": [
      {"name": "cf_clearance", "value": "...", "domain": ".bookto31.com"},
      {"name": "PHPSESSID", "value": "..."}
    ],
    "userAgent": "Mozilla/5.0 ...",
    "response": "<!DOCTYPE html>...HTML 본문...",
    "headers": {...}
  }
}
```

### 1.7 우회 검증
- `cf_clearance` 쿠키 포함 여부
- 응답 HTML에 `<title>북토끼 - 웹소설 자료실</title>` 포함
- 길이: 정상 페이지 100KB+ (challenge 페이지는 5KB)

## 2. 뉴토끼 우회 (curl_cffi)

### 2.1 차단 단계
1. **ASN 차단**: Oracle Cloud (AS31898) → NGINX_ASN
2. **국가 검증**: KR_ONLY → CloudFront geo-IP 일부 잘못 분류
3. **본문 인증**: PATCH `/api/novel/{id}/episode/{epId}` → 401 로그인 필요

### 2.2 curl_cffi TLS 위장
```python
from curl_cffi import requests as creq

session = creq.Session(impersonate="chrome120")
session.proxies = {"https": "socks5://121.169.69.116:1090"}
resp = session.get("https://toki31.com/novel/58455/5784624", timeout=15)
# status 200 OK (Python requests는 403)
```

### 2.3 결과
- 목록/메타: 성공 (curl_cffi Chrome120 fingerprint)
- 본문: PATCH 인증 필요 → **현실적 우회 불가**
- 권장: **북토끼에 동일 데이터 있으므로 사용 안 함**

## 3. 무료 KR 프록시 (fallback)

### 3.1 데이터 소스
- `https://api.proxyscrape.com/v4/free-proxy-list/get?...&country=kr`
- 5분마다 새로고침
- 작동하는 proxy만 캐시 (`alive=True`)

### 3.2 사용법 (toki31.py)
```python
PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&country=kr",
]

def _refresh_proxies(force: bool = False):
    candidates = fetch_proxy_candidates()
    verified = [c for c in candidates if verify_proxy(c)]  # 200 OK만
    _proxies = verified[:10]

def _fetch_with_failover(url: str):
    for attempt in range(MAX_ATTEMPTS):
        session = create_session_with_proxy()
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.text
        if resp.status_code in (403, 429):
            _pop_current_proxy()  # 차단된 proxy 제거
            continue
```

### 3.3 한계
- 무료 프록시는 수명이 짧음 (1-10분)
- Cloudflare Turnstile 통과 못함
- ASN이 datacenter으로 분류되어 또 차단

## 4. Rate Limiting (필수)

### 4.1 정책
- **8분** + **±2분 jitter** 같은 URL 반복 방지
- DB: `/opt/ai_data/flaresolverr/rate_limiter.db` (SQLite)
- 모듈: `apps/backend/lib/rate_limiter.py`

### 4.2 구현
```python
def seconds_until_allowed(url, interval=480, db_path=None):
    last = last_request_time(url)
    if last is None:
        return 0.0
    elapsed = time.time() - last
    if elapsed >= interval:
        return 0.0
    return (interval - elapsed) + random.uniform(0, 120)
```

### 4.3 사용 패턴
- 챕터 1개 호출: `rate_limit=True` (기본값, 8분 자동 강제)
- 챕터 일괄 호출: `rate_limit=False` + 마지막에 `record_request()` 1회

## 5. 봇 차단 패턴 (회피 전략)

### 5.1 탐지 패턴
- 짧은 시간 내 동일 URL 반복 호출
- 짧은 시간 내 다른 URL 빠른 연속 호출 (스크래퍼 signature)
- User-Agent 없거나 비정상
- TLS fingerprint가 Python/requests와 일치 (JA3/JA4)
- Cloudflare 데이터센터 ASN

### 5.2 회피 전략
- ✅ **시간 간격**: 챕터별 8분 + jitter
- ✅ **쿠키/세션**: FlareSolverr가 발급한 cf_clearance 재사용
- ✅ **User-Agent**: Chrome126 실제 UA 사용
- ✅ **TLS fingerprint**: curl_cffi Chrome120 impersonate
- ✅ **헤더 일관성**: Chrome의 Sec-CH-*, Accept-Encoding 등 모두 포함

### 5.3 우리가 시도했지만 실패한 것
- ❌ Cloudflare WARP (ASN 우회만 됨, 일부 IP가 CZ로 오분류)
- ❌ Tor KR exit node (살아있는 KR 노드 0개)
- ❌ 무료 KR proxy list (대부분 datacenter ASN으로 차단)
- ❌ requests 기본 (TLS fingerprint로 즉시 차단)

## 6. 권장 운영 방식

### 일반 사용자 (브라우저)
- 직접 북토끼 접속, 자동으로 Cloudflare 통과

### 자동화 (운영자)
1. **신규 챕터 갱신 체크**: miniebook API 페이지 1 (가장 안전)
2. **신규 챕터 본문**: miniebook API
3. **fallback**: 북토끼 + FlareSolverr (rate_limit=True)
4. **주기**: 주 1-2회 (자동 갱신), 사용자 요청 시 EPUB 재생성

### 절대 하지 말 것
- 1분에 10챕터 이상 호출
- rate_limit=False로 일괄 호출 + record_request 안 함
- FlareSolverr session 매번 새로 생성 (불필요한 부하)

## 다음 문서
- [03-EPUB-GENERATION.md](03-EPUB-GENERATION.md) - EPUB 생성
- [04-API-REFERENCE.md](04-API-REFERENCE.md) - API 명세