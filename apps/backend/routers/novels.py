#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""소설 관련 API 라우터"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from services.data import get_novel_list, get_novel_detail
from services.epub import build_epub, get_novel_title

router = APIRouter()


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