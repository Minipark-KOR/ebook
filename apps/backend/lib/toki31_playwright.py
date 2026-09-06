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


def derive_key(nv_cookie: str, episode_ref: str, novel_id: str) -> bytes:
    """AES-GCM 키 파생.

    JS 코드: SHA-256(nv_cookie_bytes + f":{episode_ref}:{novel_id}:v3".encode())
    """
    token_bytes = nv_cookie.encode('utf-8') if isinstance(nv_cookie, str) else nv_cookie
    salt = f":{episode_ref}:{novel_id}:v3".encode('utf-8')
    combined = token_bytes + salt
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


async def fetch_chapter_content(
    novel_id: str,
    chapter_id: str,
    timeout_ms: int = 60000,
) -> Optional[Tuple[str, str]]:
    """Playwright로 toki31 챕터 콘텐츠 가져오기.

    Returns:
        (title, content_text) or None on failure
    """
    from playwright.async_api import async_playwright

    env = _load_proxy_env()
    proxy_user = env.get("MASKPROXY_USER", "")
    proxy_pass = env.get("MASKPROXY_PASS", "")
    proxy_host = env.get("MASKPROXY_HOST", "gw.maskproxy.io")
    proxy_port = env.get("MASKPROXY_PORT", "1288")

    if not proxy_user or not proxy_pass:
        logger.error("MASKPROXY_USER/MASKPROXY_PASS not set in .env.local")
        return None

    proxy_url = f"http://{proxy_host}:{proxy_port}"
    target_url = f"https://toki31.com/novel/{novel_id}/{chapter_id}"

    # API 응답 저장
    api_response = {}
    title_text = ""

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

        # 인터셉트: novel-content API 응답
        async def on_response(response):
            if '/api/novel-content' in response.url:
                try:
                    body = await response.json()
                    api_response['data'] = body
                    api_response['status'] = response.status
                    logger.debug(f"novel-content response: status={response.status}, ok={body.get('ok')}")
                except Exception as e:
                    logger.debug(f"Failed to parse novel-content: {e}")

        page.on("response", on_response)

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

        # API 호출 대기 (ad-ack 완료 후 자동 호출됨)
        await page.wait_for_timeout(15000)

        # 제목 추출
        title_text = await page.title()
        # "소설 제목 - 회차 | 뉴토끼" → 회차 부분만
        if ' - ' in title_text:
            parts = title_text.split(' - ')
            if len(parts) >= 2:
                title_text = parts[1].split('|')[0].strip()

        await browser.close()

    # 콘텐츠 확인
    if not api_response.get('data', {}).get('ok'):
        error = api_response.get('data', {}).get('error', 'unknown')
        status = api_response.get('status', 0)
        logger.error(f"novel-content failed: status={status}, error={error}")
        return None

    payload = api_response['data'].get('payload', '')
    if not payload:
        logger.error("novel-content: empty payload")
        return None

    # 세션 쿠키 필요 (nv) - API 호출 시 이미 사용됨
    # 하지만 우리는 인터셉트한 데이터로 복호화해야 함
    # 세션 쿠키는 브라우저 컨텍스트에서 추출해야 함

    # 대안: 브라우저에서 직접 복호화 후 결과 추출
    # 하지만 이미 브라우저를 닫았으므로, API 재호출 필요

    # 실제로는 브라우저가 이미 복호화해서 DOM에 렌더링했어야 함
    # 하지만 ad-ack 후 콘텐츠가 DOM에 렌더링되지 않는 경우가 있음
    # 이 경우 세션 쿠키를 재사용해서 직접 복호화

    # 여기서는 세션 쿠키 없이 복호화 시도
    # (nv 쿠키가 API 응답 헤더에 포함될 수 있음)
    logger.error("novel-content: need nv cookie for decryption - use fetch_chapter_content_full()")
    return None


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
    proxy_user = env.get("MASKPROXY_USER", "")
    proxy_pass = env.get("MASKPROXY_PASS", "")
    proxy_host = env.get("MASKPROXY_HOST", "gw.maskproxy.io")
    proxy_port = env.get("MASKPROXY_PORT", "1288")

    if not proxy_user or not proxy_pass:
        logger.error("MASKPROXY_USER/MASKPROXY_PASS not set in .env.local")
        return None

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
        content_text = await page.evaluate("""
            () => {
                // Shadow DOM에서 텍스트 추출
                const host = document.querySelector('.novel-content')?.parentElement;
                if (host?.__novelShadow) {
                    const shadow = host.__novelShadow;
                    const paragraphs = shadow.querySelectorAll('p');
                    if (paragraphs.length > 0) {
                        return Array.from(paragraphs).map(p => p.textContent).join('\\n\\n');
                    }
                    return shadow.textContent || '';
                }

                // fallback: 일반 DOM
                const viewer = document.querySelector('.novel-viewer');
                if (viewer) {
                    const divs = viewer.querySelectorAll('div[style*="novel-font-size"]');
                    for (const div of divs) {
                        if (div.textContent.length > 100) {
                            return div.textContent;
                        }
                    }
                }

                // 모든 긴 텍스트 노드 검색
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );
                let best = '';
                let node;
                while (node = walker.nextNode()) {
                    const t = node.textContent.trim();
                    if (t.length > best.length && /[가-힣]/.test(t) && t.length > 200) {
                        best = t;
                    }
                }
                return best;
            }
        """)

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
    rsc_token = await page.evaluate("""
        () => {
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const text = s.textContent;
                const match = text.match(/"token":"([A-Za-z0-9_-]+)"/);
                if (match) return match[1];
                // NovelContent附近的token搜索
                const idx = text.indexOf('NovelContent');
                if (idx > -1) {
                    const chunk = text.substring(Math.max(0, idx - 2000), idx + 2000);
                    const m2 = chunk.match(/"token":"([A-Za-z0-9_-]+)"/);
                    if (m2) return m2[1];
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
        key = derive_key(session_token, chapter_id, novel_id)
        decrypted = decrypt_payload(payload, key)
        return extract_text_from_content(decrypted)
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return ""
