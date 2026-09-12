#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/toki31_playwright.py
"""toki31 Playwright 콘텐츠 추출기.

toki31의 anti-bot 보호를 우회하기 위해 Playwright 브라우저를 사용:
1. 브라우저가 ad-ack (광고 확인) 자동 처리
2. /api/novel-content API 응답 인터셉트
3. AES-GCM 복호화로 콘텐츠 추출

복호화 알고리즘 (JS에서 역공학):
- Key: SHA-256(nv_cookie + f":{episode_ref}:{novel_id}:v3")
- IV: payload 앞 12 bytes
- Algorithm: AES-128-GCM

데이터 절약:
- 브라우저/컨텍스트/페이지를 프로세스 수명 동안 재사용 (회차마다 Chromium 재실행 방지
  → JS 번들·쿠키 재사용, 회차당 다운로드 급감)
- 불필요 리소스 차단: image/font/media/stylesheet → route.abort() (본문 추출엔 불필요)
- 프록시 우선순위: MaskProxy($0.87/GB) → DataImpulse($1/GB)
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from typing import Optional, Tuple

from lib.sources import get_base_url
from lib.domain_router import auto_update_base, base_of, candidate_bases, update_base_url

logger = logging.getLogger(__name__)

# .env.local에서 프록시 설정 로드
ENV_LOCAL = os.path.join(os.path.dirname(__file__), '..', '.env.local')

# 하위 호환용 모듈 상수 — 실제 사용은 _toki_base()로 최신 base_url을 읽는다.
TOKI31_BASE = get_base_url("toki31")


def _toki_base() -> str:
    """최신 toki31 base_url (도메인 자동 전환 반영)."""
    return get_base_url("toki31")

# 프록시 우선순위: DataImpulse(주력, 충전분 소모) → MaskProxy(폴백)
_PROXY_PRIORITY = ("dataimpulse", "maskproxy")
_PROXY_DEFAULTS = {
    "maskproxy": ("MASKPROXY", "gw.maskproxy.io", "1288"),
    "dataimpulse": ("DATAIMPULSE", "gw.dataimpulse.com", "823"),
}

# 본문 추출에 불필요한 리소스 → 차단 (트래픽 절약)
_BLOCKED_RESOURCE_TYPES = ("image", "font", "media", "stylesheet")

# 트래커/광고 도메인 → 차단 (본문 추출과 무관, 네트워크 절약)
# 실측: whoas.xyz/live/track.js 등. 정확한 매칭으로 오차단 방지.
_TRACKER_DOMAINS = ("whoas.xyz",)

# ad_guard_bg.wasm(403KB/회차, 사이트 anti-adblock): 차단하면 본문 추출이 실패하므로
# **차단하지 않고 로컬 캐시로 재서빙**한다 (첫 회차 1회 다운로드 → 이후 0 네트워크).
# WebAssembly.instantiateStreaming으로 로드되는 JS에는 정상 응답으로 보이므로
# anti-adblock 탐지도 트리거하지 않는다. (Playwright route.fulfill 패턴)
_WASM_CACHE_URL_MARKER = "ad_guard_bg.wasm"

# 프록시 인증/연결 실패 시 브라우저 리셋 임계값
_RESET_AFTER_CONSECUTIVE_FAILURES = 3

# 안전장치 한도 (환경변수로 오버라이드)
# - novel-content API 페이로드 상한 (정상 ~24KB, 60KB 초과 시 비정상으로 판단)
# - 회차당 총 트래픽 상한: JS/wasm 로컬 캐시 재서빙 이후 실측
#   콜드(첫 로드) ~0.94MB / 웜(캐시 히트) ~0.18MB가 정상.
#   상한은 "비정상 대용량" 안전장치로 정상 대비 여유를 두되, 캐시 전처럼
#   재다운로드 시대의 느슨한 값(2.5/2.0MB)보다 타이트하게 잡는다.
#   (캐시 미스로 JS를 일부 재다운로드해도 웜 0.8MB 안에서 수용)
_NOVEL_CONTENT_MAX_BYTES = int(float(os.getenv('TOKI31_CONTENT_MAX_KB', '60'))) * 1024
_CHAPTER_COLD_MAX_BYTES = int(float(os.getenv('TOKI31_CHAPTER_COLD_MAX_MB', '1.5')) * 1024 * 1024)
_CHAPTER_WARM_MAX_BYTES = int(float(os.getenv('TOKI31_CHAPTER_WARM_MAX_MB', '0.8')) * 1024 * 1024)


def _load_proxy_env():
    """Load proxy credentials from .env.local."""
    env = {}
    if os.path.exists(ENV_LOCAL):
        with open(ENV_LOCAL) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env


def b64url_decode(s: str) -> bytes:
    """Decode base64url string to bytes."""
    s = s.replace("-", "+").replace("_", "/")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.b64decode(s)


def b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string."""
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


def derive_key(nv_cookie: str, novel_id: str, episode_id: str) -> bytes:
    """AES-GCM 키 파생.

    JS 코드 (역공학):
        let r = [e, new TextEncoder().encode(`:${t}:${n}:v3`)];
        keyMaterial = r[0] + r[1]  // e (bytes) + ":t:n:v3" (bytes)
        key = SHA-256(keyMaterial)

    여기서:
        e = base64url_decode(nv_cookie.split('.')[0])  // nv 쿠키의 첫 부분 디코드
        t = novelId
        n = episodeId (episodeRef 아님!)

    주의: 기존 구현과 달리 episodeRef가 아닌 episodeId를 사용.
    """
    # nv 쿠키의 첫 부분 (before '.')을 base64url 디코드
    part1_b64 = nv_cookie.split('.')[0]
    padding = 4 - len(part1_b64) % 4
    if padding != 4:
        part1_b64 += '=' * padding
    part1_bytes = base64.urlsafe_b64decode(part1_b64)

    salt = f":{novel_id}:{episode_id}:v3".encode('utf-8')
    combined = part1_bytes + salt
    return hashlib.sha256(combined).digest()


def decrypt_payload(payload_b64: str, key: bytes) -> str:
    """AES-GCM 복호화.

    Payload format: base64url(IV[12] || ciphertext+tag[16])
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = b64url_decode(payload_b64)
    if len(raw) < 28:
        raise ValueError(f"Payload too short: {len(raw)} bytes (need >= 28)")

    iv = raw[:12]
    ciphertext = raw[12:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode('utf-8')


def extract_text_from_content(content_json: str) -> str:
    """콘텐츠 JSON에서 본문 텍스트 추출.

    Returns:
        추출된 본문 텍스트
    """
    try:
        data = json.loads(content_json)
    except json.JSONDecodeError:
        # JSON이 아닌 경우 그대로 반환
        return content_json

    kind = data.get('kind', 'unknown')

    if kind == 'text' and isinstance(data.get('paragraphs'), list):
        return '\n\n'.join(data['paragraphs'])

    elif kind == 'html' and isinstance(data.get('html'), str):
        html = data['html']
        # HTML 태그 제거
        text = re.sub(r'<br\s*/?>', '\n', html)
        text = re.sub(r'<p[^>]*>', '\n', text)
        text = re.sub(r'</p>', '', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&amp;(nbsp|amp|quot|apos|lt|gt|#\d{1,7}|#x[0-9a-fA-F]{1,6});',
                       lambda m: {'nbsp': ' ', '&': '&', '"': '"', "'": "'",
                                  '<': '<', '>': '>'}.get(m.group(1), m.group(0)),
                       text)
        return text.strip()

    elif kind == 'text-shuffled' and isinstance(data.get('paragraphs'), list):
        paragraphs = data['paragraphs']
        perm = data.get('perm', [])
        if perm and len(perm) == len(paragraphs):
            # Unshuffle
            unshuffled = [''] * len(paragraphs)
            for i, p_idx in enumerate(perm):
                if 0 <= p_idx < len(paragraphs):
                    unshuffled[p_idx] = paragraphs[i]
            return '\n\n'.join(unshuffled)
        return '\n\n'.join(paragraphs)

    return str(data)


class Toki31Collector:
    """toki31 챕터 수집기 — 브라우저 재사용 + 리소스 차단.

    브라우저/컨텍스트/페이지를 수명 동안 유지해 JS 번들·쿠키를 재사용한다.
    (회차마다 Chromium을 새로 띄우면 JS 번들(수백 KB)을 매번 재다운로드)
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._proxy = None
        self._content_payload = {}
        self._response_event = asyncio.Event()
        self._consecutive_failures = 0
        self._traffic_total = 0  # 프록시로 받은 총 응답 바이트 (실측, 수명 누적)
        self._is_cold = True  # 브라우저 첫 로드 여부 (JS 번들 전체 다운로드 → 상한 높게)
        self._wasm_cache = {}  # ad_guard_bg.wasm url → bytes (챕터 간 재서빙)
        self._js_cache = {}  # JS 청크 url → bytes (검증: 동일 URL 내용 안정)
        self._js_cache_hits = set()  # 캐시로 재서빙된 JS url (계상 제외용)
        self._resolve_proxy()

    # --- 프록시 ---

    def _resolve_proxy(self):
        """프록시 설정 해석 — MaskProxy 우선, DataImpulse 백업."""
        env = _load_proxy_env()
        for name in _PROXY_PRIORITY:
            prefix, default_host, default_port = _PROXY_DEFAULTS[name]
            user = env.get(f"{prefix}_USER", "")
            password = env.get(f"{prefix}_PASS", "")
            host = env.get(f"{prefix}_HOST", default_host)
            port = env.get(f"{prefix}_PORT", default_port)
            if not user or not password:
                continue
            # 한국 IP targeting (DataImpulse만 지원)
            if name == "dataimpulse" and "__cr." not in user:
                user = user + "__cr.kr"
            self._proxy = {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": password,
            }
            logger.info(f"toki31 프록시 선택: {name} ({host}:{port})")
            return
        logger.error("toki31 프록시 자격증명 없음 (.env.local)")

    @property
    def has_proxy(self) -> bool:
        return bool(self._proxy)

    # --- 브라우저 수명 ---

    async def _ensure_started(self) -> None:
        """브라우저 1회 실행 (재사용). 이미 실행 중이면 no-op."""
        if self._browser:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        launch_kwargs = {"headless": True}
        if self._proxy:
            launch_kwargs["proxy"] = self._proxy
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        self._page = await self._context.new_page()

        # novel-content API 응답 리스너 (브라우저 수명 동안 1회만 등록)
        self._content_payload = {}
        self._response_event = asyncio.Event()

        async def _on_response(response):
            # 트래픽 실측: content-length 우선, 없으면 body 크기 (리소스 차단 후
            # 남는 응답은 HTML/JS/API뿐이라 프록시 과금 바이트의 좋은 근사)
            # 캐시 히트(디스크/메모리)는 네트워크 바이트가 0이므로 집계 제외 —
            # 안 그러면 브라우저 재사용 시 JS 재다운로드가 실제보다 크게 잡혀
            # 웜 상한을 오초과해 정상 챕터가 차단된다.
            # ad_guard_bg.wasm은 캐시 재서빙(route.fulfill)으로 네트워크 바이트가
            # 0 (첫 1회만 실다운로드). route.fulfill 응답은 requestStart=-1이라
            # timing 기반 캐시 판정이 안 되므로 URL로 직접 제외.
            if _WASM_CACHE_URL_MARKER in response.url:
                return
            # 캐시로 재서빙된 JS도 0 네트워크 → 계상 제외
            if response.url in self._js_cache_hits:
                return
            try:
                tm = response.request.timing
                if tm:
                    rs = tm.get('responseStart')
                    rq = tm.get('requestStart')
                    # 캐시 히트: 두 시각 모두 존재·0 이상이고 요청 지속시간이 ~0ms
                    if (isinstance(rs, (int, float)) and isinstance(rq, (int, float))
                            and rs >= 0 and rq >= 0 and (rs - rq) < 1):
                        return
            except Exception:
                pass
            try:
                cl = response.headers.get('content-length')
                if cl and cl.isdigit():
                    self._traffic_total += int(cl)
                else:
                    self._traffic_total += len(await response.body())
            except Exception:
                pass
            if '/api/novel-content' in response.url:
                try:
                    data = await response.json()
                    if data.get('ok') and data.get('payload'):
                        payload = data['payload']
                        payload_bytes = len(payload)
                        # 안전장치: 본문 페이로드가 비정상 대용량이면 차단 (정상 ~24KB)
                        if payload_bytes > _NOVEL_CONTENT_MAX_BYTES:
                            logger.warning(
                                f"novel-content 페이로드 비정상 대용량: {payload_bytes}B "
                                f"(한도 {_NOVEL_CONTENT_MAX_BYTES}B) — 차단"
                            )
                            self._content_payload['oversized'] = True
                        else:
                            self._content_payload['data'] = data
                            self._response_event.set()
                            logger.debug(f"novel-content response captured ({payload_bytes}B)")
                except Exception:
                    pass

        self._page.on("response", _on_response)

        # 불필요한 리소스 차단 (image/font/media/stylesheet → abort, 트래픽 절약)
        await self._page.route("**/*", self._block_unnecessary)
        logger.info("toki31 브라우저 시작 완료 (리소스 차단 + 재사용)")

    async def _block_unnecessary(self, route, request):
        """불필요한 리소스 차단 — 이미지/폰트/미디어/CSS 차단, JS만 허용.

        추가:
        - 트래커/광고 도메인 차단 (whoas.xyz 등)
        - ad_guard_bg.wasm(anti-adblock)은 **로컬 캐시 재서빙** — 차단 대신
          첫 요청만 다운로드해 캐시하고, 이후 요청은 메모리에서 응답한다.
          (콘텐츠 추출에 필수라 차단 불가하지만 403KB/회차 재다운로드를 없앤다)
        """
        url = request.url
        resource_type = request.resource_type

        # 1) 트래커/광고 도메인 차단
        if any(td in url for td in _TRACKER_DOMAINS):
            await route.abort()
            return

        # 2) JS 청크 — 첫 다운로드 후 로컬 캐시 재서빙 (0 네트워크)
        #    (검증: 동일 URL의 내용은 챕터 간 안정 — 캐시 안전)
        if resource_type == "script":
            cached = self._js_cache.get(url)
            if cached is not None:
                self._js_cache_hits.add(url)
                await route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body=cached,
                )
                return
            try:
                resp = await route.fetch()
                self._js_cache[url] = await resp.body()
                await route.fulfill(response=resp)
                return
            except Exception:
                pass

        # 3) ad_guard_bg.wasm — 첫 다운로드 후 캐시 재서빙
        if _WASM_CACHE_URL_MARKER in url:
            cached = self._wasm_cache.get(url)
            if cached is not None:
                await route.fulfill(
                    status=200,
                    content_type="application/wasm",
                    body=cached,
                )
                return
            try:
                resp = await route.fetch()
                self._wasm_cache[url] = await resp.body()
                await route.fulfill(response=resp)
                return
            except Exception:
                # 캐시 실패 시 그대로 통과 (다운로드) — 추출 우선
                pass

        # 3) 미디어 계열 차단
        if resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    async def _close_browser(self) -> None:
        """브라우저 정리 (다음 호출에서 재실행)."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._content_payload = {}
        self._response_event = asyncio.Event()
        self._is_cold = True  # 리셋 후 JS 번들 재다운로드 → 콜드 상한 적용

    # --- 챕터 수집 ---

    async def collect_chapter(
        self,
        novel_id: str,
        chapter_id: str,
        timeout_ms: int = 60000,
    ) -> Optional[Tuple[str, str]]:
        """단일 챕터 본문 수집.

        Returns:
            (title, content_text) or None on failure
        """
        if not self._proxy:
            logger.error("toki31 프록시 자격증명 없음 — 수집 불가")
            return None

        await self._ensure_started()
        page = self._page

        # 회차별 트래픽 한도 — 첫 로드(콜드)는 JS 번들 전체로 상한 높게, 이후(웜)는 낮게
        is_cold = self._is_cold
        chapter_start_bytes = self._traffic_total
        traffic_cap = _CHAPTER_COLD_MAX_BYTES if is_cold else _CHAPTER_WARM_MAX_BYTES

        # 이벤트 초기화 (이전 회차 응답 무시)
        self._response_event.clear()
        self._content_payload.clear()

        target_url = f"{_toki_base().rstrip('/')}/novel/{novel_id}/{chapter_id}"

        # 페이지 로드 (재시도 포함)
        loaded = False
        fatal_proxy_error = False
        for attempt in range(3):
            try:
                resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                if resp and resp.status == 200:
                    loaded = True
                    break
            except Exception as e:
                msg = str(e)
                logger.warning(f"Page load attempt {attempt + 1} failed: {msg}")
                if any(k in msg for k in ("ERR_PROXY", "PROXY_AUTH", "proxy authentication")):
                    # 프록시 인증/연결 문제 → 브라우저 리셋 필요
                    fatal_proxy_error = True
                    break
                if attempt < 2:
                    await page.wait_for_timeout(3000)

        # 현재 도메인 사망 가능성 → 미러 도메인(base_url 도메인 목록)으로 재시도
        if not loaded and not fatal_proxy_error:
            old_base = base_of(target_url)
            for base in candidate_bases("toki31"):
                if base == old_base:
                    continue
                alt_url = f"{base.rstrip('/')}/novel/{novel_id}/{chapter_id}"
                try:
                    resp = await page.goto(alt_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if resp and resp.status == 200:
                        loaded = True
                        target_url = alt_url
                        update_base_url("toki31", base)
                        logger.warning(f"🔄 [toki31] 미러 페일오버: {old_base} → {base}")
                        break
                except Exception as e:
                    msg = str(e)
                    logger.warning(f"Mirror load failed ({base}): {msg}")
                    if any(k in msg for k in ("ERR_PROXY", "PROXY_AUTH", "proxy authentication")):
                        fatal_proxy_error = True
                        break

        if not loaded:
            logger.error(f"Failed to load {target_url} after 3 attempts")
            if fatal_proxy_error:
                self._consecutive_failures += 1
                if self._consecutive_failures >= _RESET_AFTER_CONSECUTIVE_FAILURES:
                    logger.warning("프록시 연속 실패 — 브라우저 리셋")
                    await self._close_browser()
                    self._consecutive_failures = 0
            else:
                self._consecutive_failures = 0
            return None

        # 리다이렉트 최종 URL 감지 → sources.json base_url 자동 갱신
        # (구 도메인이 새 도메인으로 301/302 리다이렉트하는 동안 이를 활용)
        try:
            resolved = page.url
            auto_update_base("toki31", target_url, resolved)
        except Exception:
            pass

        self._consecutive_failures = 0

        # 첫 로드 성공 → JS 번들 캐시 완료. 이후 회차는 웜(낮은 상한) 적용
        self._is_cold = False

        # 제목 추출 (페이지 타이틀)
        title_text = await page.title()
        if ' - ' in title_text:
            parts = title_text.split(' - ')
            if len(parts) >= 2:
                title_text = parts[1].split('|')[0].strip()

        # novel-content API 응답 대기 (ad-ack 완료 후 브라우저가 자동 호출, 최대 25s)
        if not self._content_payload.get('data'):
            try:
                await asyncio.wait_for(self._response_event.wait(), timeout=25)
            except asyncio.TimeoutError:
                pass

        if self._content_payload.get('oversized'):
            logger.error("novel-content 페이로드 비정상 대용량 — 회차 차단")
            return None

        if not self._content_payload.get('data'):
            logger.error("novel-content API response not received within 25s")
            return None

        # nv 쿠키 추출
        cookies = await self._context.cookies()
        nv_cookie = ""
        for c in cookies:
            if c['name'] == 'nv':
                nv_cookie = c['value']
                break

        if not nv_cookie:
            logger.error("nv cookie not found")
            return None

        payload = self._content_payload['data'].get('payload', '')
        if not payload:
            logger.error("Empty payload from novel-content")
            return None

        # 복호화
        try:
            key = derive_key(nv_cookie, novel_id, chapter_id)
            decrypted = decrypt_payload(payload, key)
            content_text = extract_text_from_content(decrypted)
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

        if not content_text or len(content_text) < 50:
            logger.error(f"Failed to extract content from {target_url}")
            return None

        # 회차별 트래픽 한도 점검 — 비정상 대용량이면 차단 (정상: 콜드 ~1MB / 웜 ~430KB)
        chapter_bytes = self._traffic_total - chapter_start_bytes
        if chapter_bytes > traffic_cap:
            logger.warning(
                f"회차 트래픽 비정상 대용량: {chapter_bytes / 1024:.0f}KB "
                f"(한도 {traffic_cap / 1024 / 1024:.1f}MB, {'콜드' if is_cold else '웜'}) — 회차 차단"
            )
            return None

        return (title_text, content_text.strip())


# 모듈 수준 싱글턴 — 프로세스 수명 동안 브라우저 재사용 (데이터 절약)
_collector: Optional[Toki31Collector] = None
_collector_loop: Optional[asyncio.AbstractEventLoop] = None


def get_traffic_total_bytes() -> int:
    """프로세스 수명 동안 프록시로 다운로드한 총 바이트 (실측 누적).

    pipeline의 트래픽 가드가 collect 전후 delta를 계산해 일일 한도에 반영한다.
    """
    return _collector._traffic_total if _collector else 0


def get_collector_state() -> dict:
    """현재 collector 상태 (트래픽 분석용)."""
    if not _collector:
        return {"is_cold": True, "traffic_total": 0, "js_cache": 0, "wasm_cache": 0, "js_hits": 0}
    return {
        "is_cold": _collector._is_cold,
        "traffic_total": _collector._traffic_total,
        "js_cache": len(_collector._js_cache),
        "wasm_cache": len(_collector._wasm_cache),
        "js_hits": len(_collector._js_cache_hits),
    }


def fetch_chapter_content_full(
    novel_id: str,
    chapter_id: str,
    timeout_ms: int = 60000,
) -> Optional[Tuple[str, str]]:
    """toki31 챕터 콘텐츠 완전 추출 (동기 인터페이스, 브라우저 재사용).

    모듈 싱글턴 Toki31Collector를 고정 이벤트 루프에서 재사용한다.
    (호출마다 이벤트 루프/브라우저를 새로 만들지 않음)

    Returns:
        (title, content_text) or None on failure
    """
    global _collector, _collector_loop

    if _collector is None:
        _collector = Toki31Collector()

    if not _collector.has_proxy:
        logger.error("toki31 프록시 자격증명 없음 (.env.local)")
        return None

    if _collector_loop is None or _collector_loop.is_closed():
        _collector_loop = asyncio.new_event_loop()

    try:
        return _collector_loop.run_until_complete(
            _collector.collect_chapter(novel_id, chapter_id, timeout_ms)
        )
    except Exception as e:
        logger.error(f"toki31 collect 실패: {type(e).__name__}: {e}")
        return None