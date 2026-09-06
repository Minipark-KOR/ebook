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

    ad-ack 처리 + API intercept + AES-GCM 복호화를 순차 진행.
    novel-content API 응답을 인터셉트하여 복호화한다.

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

        # novel-content API 응답을 인터셉트 (goto 전에 설정)
        content_payload = {}

        async def on_response(response):
            if '/api/novel-content' in response.url:
                try:
                    data = await response.json()
                    if data.get('ok') and data.get('payload'):
                        content_payload['data'] = data
                        logger.debug("novel-content response captured")
                except Exception:
                    pass

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

        # 제목 추출 (페이지 타이틀)
        title_text = await page.title()
        if ' - ' in title_text:
            parts = title_text.split(' - ')
            if len(parts) >= 2:
                title_text = parts[1].split('|')[0].strip()

        # novel-content API 응답 대기 (ad-ack 완료 후 브라우저가 자동 호출)
        for _ in range(25):
            if content_payload.get('data'):
                break
            await page.wait_for_timeout(1000)

        if not content_payload.get('data'):
            logger.error("novel-content API response not received within 25s")
            await browser.close()
            return None

        # nv 쿠키 추출
        cookies = await context.cookies()
        nv_cookie = ""
        for c in cookies:
            if c['name'] == 'nv':
                nv_cookie = c['value']
                break

        if not nv_cookie:
            logger.error("nv cookie not found")
            await browser.close()
            return None

        payload = content_payload['data'].get('payload', '')
        if not payload:
            logger.error("Empty payload from novel-content")
            await browser.close()
            return None

        # 복호화
        try:
            key = derive_key(nv_cookie, novel_id, chapter_id)
            decrypted = decrypt_payload(payload, key)
            content_text = extract_text_from_content(decrypted)
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            await browser.close()
            return None

        await browser.close()

    if not content_text or len(content_text) < 50:
        logger.error(f"Failed to extract content from {target_url}")
        return None

    return (title_text, content_text.strip())
