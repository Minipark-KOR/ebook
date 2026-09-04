#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""회차 관련 API 라우터"""

from fastapi import APIRouter, HTTPException

from services.data import get_chapter_list, get_chapter_detail

router = APIRouter()


@router.get("/novels/{novel_id}/chapters")
async def get_chapters(novel_id: str, page: int = 1, limit: int = 20):
    """회차 목록 조회"""
    result = get_chapter_list(novel_id, page, limit)
    return result


@router.get("/chapters/{wr_id}")
async def get_chapter(wr_id: int):
    """회차 상세 조회"""
    chapter = get_chapter_detail(wr_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return chapter
