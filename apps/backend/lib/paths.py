#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/paths.py
"""미디어 타입별 저장 경로 해석 — 단일 진실 원천.

데이터 레이아웃:
    /opt/ai_data/flaresolverr/
    ├── novels/{novel_id}/*.json     (media_type="novel", 기본)
    ├── comics/{novel_id}/*.json     (media_type="comic")
    └── webtoons/{novel_id}/*.json   (media_type="webtoon")

novel_id는 작품 제목의 공백/슬래시를 '_'로 치환한 디렉토리명이며, 세 폴더에
걸쳐 유일하다고 가정한다(충돌 시 MEDIA_TYPES 순서상 먼저 매칭되는 폴더 우선).
"""

from pathlib import Path
from typing import Iterator, Optional

LIBRARY_ROOT = Path("/opt/ai_data/flaresolverr")

NOVELS_DIR = LIBRARY_ROOT / "novels"
COMICS_DIR = LIBRARY_ROOT / "comics"
WEBTOONS_DIR = LIBRARY_ROOT / "webtoons"

DEFAULT_MEDIA_TYPE = "novel"

# 순서 중요: 조회 시 먼저 매칭되는 폴더가 우선 (novel → comic → webtoon)
MEDIA_DIRS: dict[str, Path] = {
    "novel": NOVELS_DIR,
    "comic": COMICS_DIR,
    "webtoon": WEBTOONS_DIR,
}

MEDIA_TYPES = tuple(MEDIA_DIRS.keys())


def normalize_media_type(media_type: Optional[str]) -> str:
    """알 수 없는/누락된 media_type은 기본값(novel)으로 정규화."""
    mt = (media_type or "").strip().lower()
    return mt if mt in MEDIA_DIRS else DEFAULT_MEDIA_TYPE


def media_dir(media_type: Optional[str]) -> Path:
    """media_type → 저장 루트 디렉토리."""
    return MEDIA_DIRS[normalize_media_type(media_type)]


def novel_id_from_title(novel_title: str) -> str:
    """작품 제목 → 디렉토리명(novel_id)."""
    if not novel_title:
        return "unknown"
    return novel_title.replace(" ", "_").replace("/", "_")


def novel_dir_for(novel_title: str, media_type: Optional[str] = None) -> Path:
    """작품 제목 + media_type → 저장 디렉토리 경로 (존재 여부 무관)."""
    return media_dir(media_type) / novel_id_from_title(novel_title)


def iter_media_dirs() -> Iterator[tuple[str, Path]]:
    """존재하는 (media_type, 루트 디렉토리) 쌍을 순회."""
    for mt in MEDIA_TYPES:
        d = MEDIA_DIRS[mt]
        if d.exists():
            yield mt, d


def iter_novel_dirs() -> Iterator[tuple[str, Path]]:
    """모든 media 폴더의 작품 디렉토리를 (media_type, 경로)로 순회.

    숨김 디렉토리(.으로 시작)는 제외한다.
    """
    for mt, root in iter_media_dirs():
        for p in sorted(root.iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                yield mt, p


def find_novel_dir(novel_id: str) -> Optional[Path]:
    """novel_id로 작품 디렉토리를 검색 (novel → comic → webtoon 순)."""
    for mt in MEDIA_TYPES:
        p = MEDIA_DIRS[mt] / novel_id
        if p.is_dir():
            return p
    return None


def find_novel_dir_with_type(novel_id: str) -> Optional[tuple[str, Path]]:
    """find_novel_dir + media_type을 함께 반환."""
    for mt in MEDIA_TYPES:
        p = MEDIA_DIRS[mt] / novel_id
        if p.is_dir():
            return mt, p
    return None


def resolve_novel_dir(novel_id: str, media_type: Optional[str] = None) -> Path:
    """기존 작품 디렉토리를 찾아 반환하고, 없으면 기본 media 위치 경로를 반환.

    저장 위치가 아직 없는 신규 작품을 기본(novel) 폴더에 생성하려는
    호출부에서 사용한다.
    """
    found = find_novel_dir(novel_id)
    if found:
        return found
    return media_dir(media_type) / novel_id


def ensure_media_dirs() -> None:
    """모든 media 루트 디렉토리 생성."""
    for d in MEDIA_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
