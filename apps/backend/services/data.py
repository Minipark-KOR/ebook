#!/usr/bin/env python3
# Status: new
# Path: none — SQLite 기반으로 전환
"""JSON 파일 읽기 서비스 — SQLite 인덱스 기반.

JSON 파일은 소스 오브 데이터로 유지.
SQLite는 메타데이터/챕터 인덱스를 관리하여 빠른 조회 보장.
캐시 계층을 통해 반복적인 DB 조회를 방지.
"""

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from lib.database import get_connection, init_db, DB_PATH
from lib.paths import (
    normalize_media_type,
    find_novel_dir,
    find_novel_dir_with_type,
    iter_novel_dirs,
)


COVERS_DIR = Path("/opt/ai_data/flaresolverr/covers")


# ============================================================
# TTL 캐시 — 5분 간격으로 갱신
# ============================================================

_cache: dict = {}
_cache_ts: dict = {}
CACHE_TTL = 300  # 5분


def _get_cached(key: str, factory, ttl: int = CACHE_TTL):
    """TTL 기반 캐시 조회."""
    now = time.time()
    if now - _cache_ts.get(key, 0) < ttl:
        return _cache.get(key)
    result = factory()
    _cache[key] = result
    _cache_ts[key] = now
    return result


def invalidate_cache(prefix: str = ""):
    """캐시 무효화."""
    keys_to_delete = [k for k in _cache if k.startswith(prefix)]
    for k in keys_to_delete:
        del _cache[k]
        del _cache_ts[k]


# ============================================================
# 유틸리티
# ============================================================

def cover_url_for(novel_id: str, fallback: Optional[str] = None) -> Optional[str]:
    """로컬 표지 파일이 있으면 /api/covers 경로로, 없으면 기존 coverUrl 폴백."""
    for ext in (".webp", ".jpg", ".jpeg", ".png"):
        p = COVERS_DIR / f"{novel_id}{ext}"
        if p.exists():
            return f"/api/covers/{quote(novel_id + ext)}"
    return fallback


def _row_to_novel(row: sqlite3.Row) -> dict:
    """SQLite Row → Novel 딕셔너리 변환."""
    novel = dict(row)
    novel["mediaType"] = novel["media_type"]
    novel["totalChapters"] = novel["total_chapters"]
    novel["genre"] = json.loads(novel.get("genre") or "[]")
    novel["coverUrl"] = cover_url_for(novel["id"], novel.get("cover_url"))
    return novel


# ============================================================
# SQLite 기반 함수
# ============================================================

def get_novel_list(media_type: Optional[str] = None) -> list[dict]:
    """작품 목록 조회 — SQLite + 캐시."""
    want = normalize_media_type(media_type) if media_type else None
    cache_key = f"novel_list:{want or 'all'}"

    def _fetch():
        conn = get_connection()
        try:
            if want:
                rows = conn.execute(
                    "SELECT * FROM novels WHERE media_type=? ORDER BY title",
                    (want,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM novels ORDER BY title").fetchall()
            return [_row_to_novel(r) for r in rows]
        finally:
            conn.close()

    return _get_cached(cache_key, _fetch)


def get_novel_detail(novel_id: str) -> Optional[dict]:
    """작품 상세 조회 — SQLite + 캐시."""
    cache_key = f"novel_detail:{novel_id}"

    def _fetch():
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM novels WHERE id=?", (novel_id,)).fetchone()
            return _row_to_novel(row) if row else None
        finally:
            conn.close()

    return _get_cached(cache_key, _fetch)


def get_chapter_list(novel_id: str, page: int = 1, limit: int = 20) -> dict:
    """회차 목록 조회 — SQLite 인덱스 + 캐시."""
    cache_key = f"chapter_list:{novel_id}:{page}:{limit}"

    def _fetch():
        conn = get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM chapters WHERE novel_id=?", (novel_id,)
            ).fetchone()[0]

            start = (page - 1) * limit
            rows = conn.execute(
                """SELECT wr_id, chapter, title, content_length
                   FROM chapters
                   WHERE novel_id=?
                   ORDER BY
                     CASE WHEN chapter IS NOT NULL AND chapter > 0 THEN 0 ELSE 1 END,
                     COALESCE(chapter, wr_id)
                   LIMIT ? OFFSET ?""",
                (novel_id, limit, start),
            ).fetchall()

            data = [
                {
                    "wr_id": r["wr_id"],
                    "chapter": r["chapter"],
                    "title": r["title"],
                    "contentLength": r["content_length"],
                }
                for r in rows
            ]

            return {
                "data": data,
                "pagination": {"page": page, "limit": limit, "total": total},
            }
        finally:
            conn.close()

    return _get_cached(cache_key, _fetch, ttl=60)  # 목록은 1분 캐시


def extract_images_from_content(content: str) -> list[str]:
    """마크다운 이미지 문법 ![alt](url) 에서 URL 추출."""
    if not content:
        return []
    pattern = r'!\[.*?\]\((https?://[^\s\)]+|/api/[^\s\)]+)\)'
    return re.findall(pattern, content)


def get_chapter_detail(wr_id: int) -> Optional[dict]:
    """회차 상세 조회 — SQLite 인덱스 + JSON 파일 읽기 + 캐시."""
    cache_key = f"chapter_detail:{wr_id}"

    def _fetch():
        conn = get_connection()
        try:
            # SQLite에서 챕터 메타데이터 조회
            row = conn.execute(
                """SELECT c.*, n.id as novel_id
                   FROM chapters c
                   JOIN novels n ON c.novel_id = n.id
                   WHERE c.wr_id=?""",
                (wr_id,),
            ).fetchone()

            if not row:
                return None

            novel_id = row["novel_id"]
            content_file = row["content_file"]

            # JSON 파일에서 본문 읽기
            novel_dir = find_novel_dir(novel_id)
            if not novel_dir:
                return None

            chapter_file = novel_dir / content_file
            if not chapter_file.exists():
                return None

            with open(chapter_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            content = data.get("content", "")
            images = extract_images_from_content(content)

            # 이전/다음 회차 — SQLite 인덱스에서 바로 조회
            current_chapter = row["chapter"]
            if current_chapter is not None and current_chapter > 0:
                prev_row = conn.execute(
                    """SELECT wr_id FROM chapters
                       WHERE novel_id=? AND chapter IS NOT NULL AND chapter > 0 AND chapter < ?
                       ORDER BY chapter DESC LIMIT 1""",
                    (novel_id, current_chapter),
                ).fetchone()
                next_row = conn.execute(
                    """SELECT wr_id FROM chapters
                       WHERE novel_id=? AND chapter IS NOT NULL AND chapter > 0 AND chapter > ?
                       ORDER BY chapter ASC LIMIT 1""",
                    (novel_id, current_chapter),
                ).fetchone()
            else:
                prev_row = conn.execute(
                    """SELECT wr_id FROM chapters
                       WHERE novel_id=? AND wr_id < ?
                       ORDER BY wr_id DESC LIMIT 1""",
                    (novel_id, wr_id),
                ).fetchone()
                next_row = conn.execute(
                    """SELECT wr_id FROM chapters
                       WHERE novel_id=? AND wr_id > ?
                       ORDER BY wr_id ASC LIMIT 1""",
                    (novel_id, wr_id),
                ).fetchone()

            return {
                "wr_id": data.get("wr_id"),
                "chapter": data.get("chapter"),
                "title": data.get("title"),
                "content": content,
                "images": images,
                "prevChapter": prev_row["wr_id"] if prev_row else None,
                "nextChapter": next_row["wr_id"] if next_row else None,
            }
        finally:
            conn.close()

    return _get_cached(cache_key, _fetch, ttl=600)  # 챕터는 10분 캐시


def resolve_status(meta: dict, novel_dir: Path) -> str:
    """연재 상태 해석 — 기존 로직 유지."""
    s = (meta.get("status") or "").strip()
    if s == "완결":
        return "완결"
    if s == "단편":
        return "단편"
    if s in ("연재중", "연재"):
        return "연재중"
    latest = ""
    for f in novel_dir.glob("*.json"):
        if f.name in ("meta.json", "_chapters_index.json") or not f.stem.isdigit():
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            t = (d.get("collected_at") or "").strip()
            if t and t > latest:
                latest = t
        except Exception:
            continue
    if latest:
        try:
            if latest.endswith("Z"):
                latest = latest[:-1] + "+00:00"
            dt = datetime.fromisoformat(latest)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - dt).total_seconds() > 14 * 86400:
                return "완결"
        except Exception:
            pass
    return "연재중"
