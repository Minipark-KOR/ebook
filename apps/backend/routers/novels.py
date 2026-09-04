#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""소설 관련 API 라우터"""

from fastapi import APIRouter

from services.data import get_novel_list, get_novel_detail

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
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Novel not found")
    return novel
