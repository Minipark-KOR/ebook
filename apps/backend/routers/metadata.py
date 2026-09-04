#!/usr/bin/env python3
# Status: experimental
# Path: main.py — 메타데이터 조회 API 라우터
"""소설 메타데이터 검색 API"""

from fastapi import APIRouter, Query, HTTPException
from services.metadata import lookup, search_novel_by_title, enrich_novel_metadata, MetadataLookupError

router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.get("/lookup")
async def lookup_metadata(
    title: str = Query(..., description="소설 제목", min_length=1),
    author: str = Query(None, description="저자명 (선택)"),
    service: str = Query("goob", description="검색 서비스: goob(Google Books), openl(OpenLibrary), brave(Brave/DuckDuckGo)")
):
    """
    단일 최고 매칭 메타데이터 조회.
    제목(+선택적 저자)로 검색하여 가장 관련도 높은 결과 1개 반환.
    """
    try:
        return enrich_novel_metadata(title, author, service).to_dict()
    except MetadataLookupError as e:
        raise HTTPException(status_code=502, detail=f"메타데이터 조회 실패: {e}")


@router.get("/search")
async def search_metadata(
    title: str = Query(..., description="소설 제목", min_length=1),
    max_results: int = Query(5, ge=1, le=20, description="최대 결과 수"),
    service: str = Query("goob", description="검색 서비스: goob(Google Books), openl(OpenLibrary), brave(Brave/DuckDuckGo)")
):
    """
    다중 결과 검색.
    제목으로 검색하여 관련도 순으로 최대 N개 반환.
    """
    try:
        results = search_novel_by_title(title, service=service, max_results=max_results)
        return [r.to_dict() for r in results]
    except MetadataLookupError as e:
        raise HTTPException(status_code=502, detail=f"메타데이터 검색 실패: {e}")
