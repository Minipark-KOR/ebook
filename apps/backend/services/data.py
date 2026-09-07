#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""JSON 파일 읽기 서비스"""

import json
import re
from pathlib import Path
from typing import Optional


DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")

# 인덱스 캐시 파일명
CHAPTERS_INDEX_FILE = "_chapters_index.json"


def rebuild_chapters_index(novel_dir: Path) -> list[dict]:
    """모든 JSON 파일을 스캔하여 챕터 목록 인덱스 재구축.

    각 요청마다 모든 파일을 열지 않고 인덱스 캐시를 사용하기 위함.
    save_chapter() 호출 시 자동 갱신됨.
    """
    chapters = []
    for json_file in sorted(novel_dir.glob("*.json")):
        if json_file.name == "meta.json" or json_file.name == CHAPTERS_INDEX_FILE:
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                chapters.append({
                    "wr_id": data.get("wr_id"),
                    "chapter": data.get("chapter"),
                    "title": data.get("title"),
                    "contentLength": data.get("content_length"),
                })
        except (json.JSONDecodeError, KeyError):
            continue

    # 인덱스 캐시 파일 저장
    index_path = novel_dir / CHAPTERS_INDEX_FILE
    index_data = {
        "updated_at": __import__("datetime").datetime.now().isoformat(),
        "chapters": chapters,
    }
    try:
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 캐시 실패는 치명적이지 않음

    return chapters


def load_chapters_index(novel_dir: Path) -> Optional[list[dict]]:
    """챕터 인덱스 캐시 로드.

    없으면 재구축 후 반환.
    """
    index_path = novel_dir / CHAPTERS_INDEX_FILE
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index_data = json.load(f)
            if "chapters" in index_data:
                return index_data["chapters"]
        except (json.JSONDecodeError, OSError):
            pass

    # 캐시 없으면 재구축
    return rebuild_chapters_index(novel_dir)


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

    # meta.json이 있으면 우선 사용
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        # 챕터 수는 실제 파일 기준으로 갱신 (meta.json, 인덱스 제외)
        chapters = [f for f in novel_dir.glob("*.json")
                    if f.name not in ("meta.json", CHAPTERS_INDEX_FILE)]
        chapter_count = len(chapters)
        meta["totalChapters"] = chapter_count
        return meta

    chapters = [f for f in novel_dir.glob("*.json")
                if f.name not in ("meta.json", CHAPTERS_INDEX_FILE)]
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
    """회차 목록 조회 (인덱스 캐시 사용)"""
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.exists():
        return {"data": [], "pagination": {"page": page, "limit": limit, "total": 0}}

    # 인덱스 캐시에서 로드
    chapters = load_chapters_index(novel_dir)

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


def extract_images_from_content(content: str) -> list[str]:
    """마크다운 이미지 문법 ![alt](url) 에서 URL 추출"""
    if not content:
        return []
    # ![alt](url) 패턴 매칭
    pattern = r'!\[.*?\]\((https?://[^\s\)]+)\)'
    return re.findall(pattern, content)


def get_chapter_detail(wr_id: int) -> Optional[dict]:
    """회차 상세 조회"""
    for novel_dir in DATA_DIR.iterdir():
        if novel_dir.is_dir():
            chapter_file = novel_dir / f"{wr_id}.json"
            if not chapter_file.exists() or chapter_file.name in ("meta.json", CHAPTERS_INDEX_FILE):
                continue
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

                    content = data.get("content", "")
                    images = extract_images_from_content(content)

                    return {
                        "wr_id": data.get("wr_id"),
                        "chapter": data.get("chapter"),
                        "title": data.get("title"),
                        "content": content,
                        "images": images,
                        "prevChapter": prev_chapter,
                        "nextChapter": next_chapter,
                    }
                except (json.JSONDecodeError, KeyError):
                    continue
    return None
