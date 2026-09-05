#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""소설 관련 API 라우터"""

from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from services.data import get_novel_list, get_novel_detail
from services.epub import build_epub, get_novel_title

router = APIRouter()


# image-proxy 라우트는 가장 먼저 (/{novel_id}보다 구체적이므로 먼저 매칭되어야 함)
@router.get("/novels/image-proxy")
async def image_proxy(url: str = Query(..., description="원본 이미지 URL")):
    """외부 이미지 프록시 (Vercel 서버리스에서 외부 도메인 이미지 로드).

    namu.wiki 등 외부 도메인 이미지를 자체 도메인으로 프록시하여
    안정적인 이미지 제공. 캐시 헤더 포함.
    """
    # 화이트리스트 (보안: 임의 사이트 차단)
    ALLOWED_DOMAINS = [
        "i.namu.wiki",
        "namu.wiki",
    ]

    # URL 검증
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL")

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.netloc not in ALLOWED_DOMAINS:
        raise HTTPException(
            status_code=403,
            detail=f"Domain not allowed: {parsed.netloc}",
        )

    # 이미지 fetch
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://namu.wiki/",
            },
            timeout=15,
            stream=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "image/webp")

        # 캐시 헤더 (1일)
        headers = {
            "Cache-Control": "public, max-age=86400, immutable",
        }

        return StreamingResponse(
            resp.iter_content(chunk_size=8192),
            media_type=content_type,
            headers=headers,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Image fetch failed: {type(e).__name__}",
        )


@router.get("/novels")
async def get_novels():
    """소설 목록 조회"""
    novels = get_novel_list()
    return {"novels": novels}


@router.get("/novels/{novel_id}")
async def get_novel(novel_id: str):
    """소설 상세 조회"""
    novel = get_novel_detail(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")
    return novel


@router.get("/novels/{novel_id}/epub")
async def download_epub(novel_id: str):
    """EPUB 다운로드.

    DB의 모든 챕터를 모아서 EPUB 파일을 생성하고 다운로드.
    한글 깨짐 방지용 GoNoto 폰트가 임베드됨.
    """
    novel = get_novel_detail(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    epub_bytes = build_epub(novel_id)
    if not epub_bytes:
        raise HTTPException(
            status_code=500,
            detail="EPUB 생성 실패 (챕터 데이터 없음)",
        )

    title = get_novel_title(novel_id)
    filename = f"{title}.epub"

    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(filename)}"
            ),
            "Content-Length": str(len(epub_bytes)),
        },
    )
