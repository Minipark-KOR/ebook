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
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# .env.local에서 프록시 설정 로드
ENV_LOCAL = os.path.join(os.path.dirname(__file__), '..', '.env.local')


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


async def fetch_chapter_content_full(
    novel_id: str,
    chapter_id: str,
    timeout_ms: int = 60000,
) -> Optional[Tuple[str, str]]:
    """Playwright로 toki31 챕터 콘텐츠 완전 추출.

    브라우저가 ad-ack을 처리하고 콘텐츠를 렌더링할 때까지 기다린 후,
    DOM에서 직접 텍스트를 추출합니다.

    Returns:
        (title, content_text) or None on failure
    """
    from playwright.async_api import async_playwright

    env = _load_proxy_env()
    # DataImpulse 우선 (한국 IP targeting 가능), MaskProxy 백업
    proxy_user = env.get("DATAIMPULSE_USER", "")
    proxy_pass = env.get("DATAIMPULSE_PASS", "")
    proxy_host = env.get("DATAIMPULSE_HOST", "gw.dataimpulse.com")
    proxy_port = env.get("DATAIMPULSE_PORT", "823")

    if not proxy_user or not proxy_pass:
        # Fallback to MaskProxy
        proxy_user = env.get("MASKPROXY_USER", "")
        proxy_pass = env.get("MASKPROXY_PASS", "")
        proxy_host = env.get("MASKPROXY_HOST", "gw.maskproxy.io")
        proxy_port = env.get("MASKPROXY_PORT", "1288")

    if not proxy_user or not proxy_pass:
        logger.error("Proxy credentials not set in .env.local")
        return None

    # 한국 IP targeting을 위해 country code 추가
    if "dataimpulse" in proxy_host and "__cr." not in proxy_user:
        proxy_user = proxy_user + "__cr.kr"

    proxy_url = f"http://{proxy_host}:{proxy_port}"
    target_url = f"https://toki31.com/novel/{novel_id}/{chapter_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy={
                "server": proxy_url,
                "username": proxy_user,
                "password": proxy_pass,
            },
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        page = await context.new_page()

        # 페이지 로드 (재시도 포함)
        loaded = False
        for attempt in range(3):
            try:
                resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                if resp and resp.status == 200:
                    loaded = True
                    break
            except Exception as e:
                logger.warning(f"Page load attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await page.wait_for_timeout(3000)

        if not loaded:
            logger.error(f"Failed to load {target_url} after 3 attempts")
            await browser.close()
            return None

        # ad-ack 완료 + 콘텐츠 렌더링 대기
        # NovelContent 컴포넌트가 콘텐츠를 Shadow DOM에 렌더링함
        logger.info(f"Waiting for content render on {target_url}...")
        await page.wait_for_timeout(20000)

        # 제목 추출
        title_text = await page.title()
        if ' - ' in title_text:
            parts = title_text.split(' - ')
            if len(parts) >= 2:
                title_text = parts[1].split('|')[0].strip()

        # Shadow DOM에서 콘텐츠 추출 시도
        # 주의: headless 모드에서는 콘텐츠가 DOM에 렌더링되지 않을 수 있음
        # 따라서 API intercept + 복호화 방식이 더 안정적
        content_text = ""

        # 짧은 대기 후 API intercept 방식으로 진행
        await page.wait_for_timeout(5000)

        # 콘텐츠가 없으면 직접 API 호출 + 복호화 시도
        if not content_text or len(content_text) < 100:
            logger.info("Shadow DOM content empty, trying API intercept + decrypt...")
            content_text = await _fetch_via_api_intercept(page, novel_id, chapter_id, context)

        await browser.close()

    if not content_text or len(content_text) < 50:
        logger.error(f"Failed to extract content from {target_url}")
        return None

    return (title_text, content_text.strip())


async def _fetch_via_api_intercept(
    page,
    novel_id: str,
    chapter_id: str,
    context,
) -> str:
    """API 인터셉트 + 직접 복호화.

    브라우저에서 /api/nv-issue와 /api/novel-content를 직접 호출하고,
    응답을 인터셉트하여 복호화합니다.
    """
    # 1. nv-issue에서 세션 토큰 가져오기
    nv_session = await page.evaluate("""
        async () => {
            const res = await fetch('/api/nv-issue', {
                method: 'POST',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'content-type': 'application/json'}
            });
            const data = await res.json();
            return data.session || null;
        }
    """)

    if not nv_session:
        logger.error("Failed to get nv session token")
        return ""

    # 2. 쿠키에서 nv 값 확인
    cookies = await context.cookies()
    nv_cookie = ""
    for c in cookies:
        if c['name'] == 'nv':
            nv_cookie = c['value']
            break

    session_token = nv_cookie or nv_session
    logger.debug(f"Session token: {session_token[:30]}...")

    # 3. RSC payload에서 token 추출
    # toki31 RSC 형식: "token","eyJ..." (이스케이프된 따옴표와 쉼표 구분자)
    rsc_token = await page.evaluate("""
        () => {
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const text = s.textContent || '';
                // 형식 1: "token","ey..." (쉼표 구분자)
                let m = text.match(/"token","(ey[A-Za-z0-9_.-]+)"/);
                if (m) return m[1];
                // 형식 2: "token":"ey..." (콜론 구분자, 이스케이프 가능)
                m = text.match(/\\?"token\\?":\\?"(ey[A-Za-z0-9_.-]+)\\?"/);
                if (m) return m[1];
                // 형식 3: NovelContent 근처 검색
                const idx = text.indexOf('episodeRef');
                if (idx > -1) {
                    const chunk = text.substring(Math.max(0, idx - 500), idx + 1000);
                    m = chunk.match(/"token","(ey[A-Za-z0-9_.-]+)"/) ||
                        chunk.match(/\\?"token\\?":\\?"(ey[A-Za-z0-9_.-]+)\\?"/);
                    if (m) return m[1];
                }
            }
            return null;
        }
    """)

    if not rsc_token:
        logger.error("Failed to extract RSC token")
        return ""

    # 4. nonce 생성 + proof 계산
    nonce_and_proof = await page.evaluate("""
        async (nvSession) => {
            const arr = new Uint8Array(24);
            crypto.getRandomValues(arr);
            const b64url = (buf) => {
                let s = '';
                for (let i = 0; i < buf.length; i++) s += String.fromCharCode(buf[i]);
                return btoa(s).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/g, '');
            };
            const nonce = b64url(arr);

            const enc = new TextEncoder();
            const key = await crypto.subtle.importKey('raw', enc.encode(nvSession),
                {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
            const sig = await crypto.subtle.sign('HMAC', key, enc.encode(nonce));
            const proof = b64url(new Uint8Array(sig));

            return {nonce, proof};
        }
    """, session_token)

    # 5. novel-content API 호출
    result = await page.evaluate("""
        async (params) => {
            const {novelId, episodeId, token, nonce, proof, nvSession} = params;
            const res = await fetch('/api/novel-content', {
                method: 'POST',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {
                    'content-type': 'application/json',
                    'x-novel-client': 'shadow-v3',
                    'x-nv-session': nvSession,
                },
                body: JSON.stringify({
                    novelId, episodeId, token, nonce, proof
                })
            });
            return {status: res.status, data: await res.json()};
        }
    """, {
        "novelId": novel_id,
        "episodeId": chapter_id,
        "token": rsc_token,
        "nonce": nonce_and_proof['nonce'],
        "proof": nonce_and_proof['proof'],
        "nvSession": session_token,
    })

    if result['status'] != 200 or not result['data'].get('ok'):
        logger.error(f"novel-content API failed: {result}")
        return ""

    payload = result['data'].get('payload', '')
    if not payload:
        logger.error("Empty payload from novel-content")
        return ""

    # 6. 복호화
    try:
        key = derive_key(session_token, novel_id, chapter_id)
        decrypted = decrypt_payload(payload, key)
        return extract_text_from_content(decrypted)
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return ""
