#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""JSON 파일 읽기 서비스"""

import json
from pathlib import Path
from typing import Optional


DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")


def get_novel_list() -> list[dict]:
    """소설 목록 조회"""
    novels = []
    for novel_dir in DATA_DIR.iterdir():
        if novel_dir.is_dir():
            meta_file = novel_dir / "meta.json"
            if meta_file.exists():
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    novels.append(meta)
            else:
                # 디렉토리 이름으로 메타데이터 생성
                chapters = list(novel_dir.glob("*.json"))
                if chapters:
                    # 첫 번째 JSON 파일에서 메타데이터 추출
                    with open(chapters[0], "r", encoding="utf-8") as f:
                        first_chapter = json.load(f)
                    
                    novels.append(
                        {
                            "id": novel_dir.name,
                            "title": novel_dir.name.replace("_", " "),
                            "author": "미상",
                            "totalChapters": len(chapters),
                            "coverUrl": None,
                        }
                    )
    return novels


def get_novel_detail(novel_id: str) -> Optional[dict]:
    """소설 상세 조회"""
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.exists():
        return None
    
    chapters = list(novel_dir.glob("*.json"))
    if not chapters:
        return None
    
    return {
        "id": novel_id,
        "title": novel_id.replace("_", " "),
        "author": "미상",
        "totalChapters": len(chapters),
        "coverUrl": None,
    }


def get_chapter_list(novel_id: str, page: int = 1, limit: int = 20) -> dict:
    """회차 목록 조회"""
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.exists():
        return {"data": [], "pagination": {"page": page, "limit": limit, "total": 0}}

    chapters = []
    for json_file in sorted(novel_dir.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                chapters.append(
                    {
                        "wr_id": data.get("wr_id"),
                        "chapter": data.get("chapter"),
                        "title": data.get("title"),
                        "contentLength": data.get("content_length"),
                    }
                )
        except (json.JSONDecodeError, KeyError):
            continue

    # 페이지네이션
    start = (page - 1) * limit
    end = start + limit
    paginated = chapters[start:end]

    return {
        "data": paginated,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": len(chapters),
        },
    }


def get_chapter_detail(wr_id: int) -> Optional[dict]:
    """회차 상세 조회"""
    for novel_dir in DATA_DIR.iterdir():
        if novel_dir.is_dir():
            chapter_file = novel_dir / f"{wr_id}.json"
            if chapter_file.exists():
                try:
                    with open(chapter_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # 이전/다음 회차 찾기
                    chapters = sorted(novel_dir.glob("*.json"))
                    current_idx = None
                    for idx, ch in enumerate(chapters):
                        if ch.stem == str(wr_id):
                            current_idx = idx
                            break

                    prev_chapter = None
                    next_chapter = None
                    if current_idx is not None:
                        if current_idx > 0:
                            prev_file = chapters[current_idx - 1]
                            prev_chapter = int(prev_file.stem)
                        if current_idx < len(chapters) - 1:
                            next_file = chapters[current_idx + 1]
                            next_chapter = int(next_file.stem)

                    return {
                        "wr_id": data.get("wr_id"),
                        "chapter": data.get("chapter"),
                        "title": data.get("title"),
                        "content": data.get("content"),
                        "prevChapter": prev_chapter,
                        "nextChapter": next_chapter,
                    }
                except (json.JSONDecodeError, KeyError):
                    continue
    return None
