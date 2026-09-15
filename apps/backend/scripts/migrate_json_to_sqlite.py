#!/usr/bin/env python3
"""JSON → SQLite 마이그레이션 스크립트.

기존 JSON 파일을 읽어서 SQLite DB에 메타데이터와 챕터 인덱스를 저장한다.
JSON 파일은 소스 오브 데이터로 유지.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.database import get_connection, init_db
from lib.paths import MEDIA_DIRS, find_novel_dir


def migrate_novels():
    """모든 작품을 SQLite에 마이그레이션."""
    init_db()
    conn = get_connection()

    try:
        for media_type, root in MEDIA_DIRS.items():
            if not root.exists():
                continue
            for novel_dir in sorted(root.iterdir()):
                if not novel_dir.is_dir() or novel_dir.name.startswith("."):
                    continue
                _migrate_novel(conn, media_type, novel_dir)
        conn.commit()
        print("마이그레이션 완료")
    finally:
        conn.close()


def _migrate_novel(conn: sqlite3.Connection, media_type: str, novel_dir: Path):
    """단일 작품 마이그레이션."""
    novel_id = novel_dir.name
    meta_file = novel_dir / "meta.json"

    # 메타데이터
    meta = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

    title = meta.get("title") or novel_id.replace("_", " ")
    author = meta.get("author", "미상")
    status = meta.get("status", "연재중")
    cover_url = meta.get("coverUrl")
    description = meta.get("description", "")
    publisher = meta.get("publisher", "")
    genre = json.dumps(meta.get("genre", []), ensure_ascii=False)
    namu_url = meta.get("namuUrl")

    # 챕터 수 계산 (meta.json, 인덱스 제외)
    chapter_files = [
        f for f in novel_dir.glob("*.json")
        if f.suffix == ".json" and f.stem.isdigit()
    ]
    total_chapters = len(chapter_files)

    # novels 테이블Upsert
    conn.execute("""
        INSERT INTO novels (id, title, author, media_type, status, cover_url,
                           description, publisher, genre, total_chapters, namu_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, author=excluded.author,
            media_type=excluded.media_type, status=excluded.status,
            cover_url=excluded.cover_url, description=excluded.description,
            publisher=excluded.publisher, genre=excluded.genre,
            total_chapters=excluded.total_chapters, namu_url=excluded.namu_url,
            updated_at=CURRENT_TIMESTAMP
    """, (novel_id, title, author, media_type, status, cover_url,
          description, publisher, genre, total_chapters, namu_url))

    # chapters 테이블 — 기존 데이터 삭제 후 재생성
    conn.execute("DELETE FROM chapters WHERE novel_id=?", (novel_id,))

    for chap_file in chapter_files:
        try:
            with open(chap_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            wr_id = data.get("wr_id")
            if wr_id is None:
                continue
            chapter_num = data.get("chapter")
            chap_title = data.get("title", "")
            content = data.get("content", "")
            collected_at = data.get("collected_at", "")

            conn.execute("""
                INSERT INTO chapters (wr_id, novel_id, chapter, title,
                                     content_file, content_length, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (wr_id, novel_id, chapter_num, chap_title,
                  chap_file.name, len(content), collected_at))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    print(f"  [{media_type}] {title} — {total_chapters}화")


if __name__ == "__main__":
    migrate_novels()
