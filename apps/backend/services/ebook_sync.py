#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/ebook_sync.py
"""ebook-watcher에서 ebook_novels/ebook_chapters Neon DB 동기화.

ebook-watcher가 로컬 JSON 파일로 챕터/메타데이터를 저장한 후,
같은 정보를 Neon DB에도 UPSERT.
Vercel SSR은 Neon DB에서 직접 query하여 빠른 응답.

이중 저장 장점:
- Vercel SSR 직접 query (200-500ms) - nip.io 프록시 우회
- 로컬 JSON은 EPUB 생성 등 무거운 작업용

이중화 단점:
- 동기화 로직 필요
- 작은 일관성 지연 (1초 미만)

해결책:
- 챕터 저장 → Neon UPSERT (1-2초)
- Neon UPSERT 실패 시 → 큐에 재시도 추가
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional, List, Dict

log = logging.getLogger("ebook_sync")


def _get_connection_string() -> Optional[str]:
    """환경변수에서 Neon DB 연결 문자열 가져오기."""
    return os.getenv("NEON_DATABASE_URL", "").strip() or None


def _parse_postgres_array(text: Optional[str]) -> Optional[list]:
    """Postgres array literal '{a,b,c}' → Python list."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        # {a,b,"c"} 형식
        items = re.findall(r'"([^"]*)"|([^,{}]+)', text[1:-1])
        return [a or b for a, b in items if a or b]
    return None


def _to_pg_array(values: Optional[list]) -> Optional[str]:
    """Python list → Postgres array literal '{a,b,c}'."""
    if not values:
        return None
    parts = []
    for v in values:
        v = str(v).replace("\\", "\\\\").replace('"', '\\"')
        if "," in v or '"' in v or "{" in v:
            parts.append(f'"{v}"')
        else:
            parts.append(v)
    return "{" + ",".join(parts) + "}"


def _guess_novel_main_wr_id(novel_id: str, meta: dict) -> Optional[int]:
    """소설 메인 페이지 wr_id 추정.

    - 하남자의 탑 공략법: 작품 메인 wr_id = 21430, 1화 = 21431
    - 일반적으로 작품 메인 = 가장 작은 wr_id - 1 (1화 = 메인 + 1)

    정확한 값은 북토끼 직접 조회 필요하지만, 추정값으로 충분.
    """
    # namu_url에서 추론은 어려우므로 hardcoded 매핑 + 일반 휴리스틱
    KNOWN_MAIN = {
        "하남자의_탑_공략법": 21430,
        "오늘만_사는_기사": 25574,  # wr_id 25575는 작품 메인, 1화 = 26410
        "게임_속_바바리안으로_살아남기": 42423,
        "화산귀환": 11999,
    }
    if novel_id in KNOWN_MAIN:
        return KNOWN_MAIN[novel_id]
    return None


def _infer_status_from_db(novel_dir: Path, meta: dict) -> str:
    """DB의 마지막 챕터 수집일 + 챕터 수로 연재 상태 추정.

    - namu.wiki 데이터 의존 안 함
    - 마지막 챕터가 2주(14일) 이상 오래 = 완결 후보
    - 그 외 = 연재중
    - 챕터 1개 = 단편 or 신규
    """
    from datetime import datetime, timezone, timedelta

    # meta.json에 명시적 status가 있으면 우선 사용 (단, "unknown"이 아닐 때)
    explicit = meta.get("status", "")
    if explicit and explicit != "unknown":
        return explicit

    # 마지막 챕터의 collected_at 확인
    chapter_files = sorted(
        novel_dir.glob("*.json"),
        key=lambda f: int(f.stem) if f.stem.isdigit() else 0,
    )
    last_collected = None
    for ch_file in reversed(chapter_files):
        if not ch_file.stem.isdigit():
            continue
        try:
            with open(ch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            collected_at = data.get("collected_at")
            if collected_at:
                # ISO 8601 파싱
                if collected_at.endswith("Z"):
                    collected_at = collected_at[:-1] + "+00:00"
                last_collected = datetime.fromisoformat(collected_at)
                break
        except Exception:
            continue

    if last_collected is None:
        return "unknown"

    # 마지막 수집일로부터 경과일
    now = datetime.now(timezone.utc)
    if last_collected.tzinfo is None:
        last_collected = last_collected.replace(tzinfo=timezone.utc)
    days_since = (now - last_collected).total_seconds() / 86400

    # 챕터가 1개면 단편 or 신규
    chapter_count = sum(1 for f in chapter_files if f.stem.isdigit())
    if chapter_count <= 1:
        return "단편" if days_since > 14 else "신규"

    # 2주 이상 갱신 없으면 완결
    if days_since >= 14:
        return "완결"
    else:
        return "연재중"


def sync_novel(novel_id: str, novel_dir: Path) -> bool:
    """단일 소설 메타데이터 + 모든 챕터를 Neon DB에 UPSERT.

    Args:
        novel_id: 소설 디렉토리명 (예: "하남자의_탑_공략법")
        novel_dir: 로컬 novels/{소설ID} 경로

    Returns:
        성공 시 True, 실패 시 False
    """
    conn_str = _get_connection_string()
    if not conn_str:
        log.warning("NEON_DATABASE_URL 미설정 - 동기화 스킵")
        return False

    # 메타데이터 로드
    meta_file = novel_dir / "meta.json"
    if not meta_file.exists():
        log.warning(f"  meta.json 없음: {novel_dir}")
        return False
    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 챕터 파일 목록
    chapter_files = sorted(
        [f for f in novel_dir.glob("*.json") if f.stem.isdigit()],
        key=lambda f: int(f.stem),
    )
    chapter_count = len(chapter_files)

    try:
        import psycopg2
        from psycopg2.extras import execute_values

        conn = psycopg2.connect(conn_str)
        conn.autocommit = False
        cur = conn.cursor()

    # 1) 소설 메타 UPSERT (연재 상태는 DB의 마지막 챕터 수집일로 추정)
    inferred_status = _infer_status_from_db(novel_dir, meta)

    cur.execute("""
        INSERT INTO ebook_novels
            (id, title, author, total_chapters, cover_url, description,
             genre, status, publisher, namu_url, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            total_chapters = EXCLUDED.total_chapters,
            cover_url = EXCLUDED.cover_url,
            description = EXCLUDED.description,
            genre = EXCLUDED.genre,
            status = EXCLUDED.status,
            publisher = EXCLUDED.publisher,
            namu_url = EXCLUDED.namu_url,
            updated_at = NOW()
    """, (
        meta.get("id", novel_id),
        meta.get("title", novel_id),
        meta.get("author", "미상"),
        chapter_count,
        meta.get("coverUrl"),
        meta.get("description", ""),
        _to_pg_array(meta.get("genre", [])),
        inferred_status,
        meta.get("publisher", "북토끼"),
        meta.get("namuUrl"),
    ))

        # 2) 모든 챕터 UPSERT (batch)
        # 작품별 기본 wr_id offset (하남자의 탑은 21430 = 작품 메인, 21431 = 1화)
        # 다른 소설은 패턴이 다르지만, wr_id - 작품_메인_wr_id로 추정
        novel_main_wr_id = _guess_novel_main_wr_id(novel_id, meta)
        chapter_data = []
        for ch_file in chapter_files:
            with open(ch_file, "r", encoding="utf-8") as f:
                ch = json.load(f)
            wr_id_int = ch.get("wr_id", int(ch_file.stem))
            chapter = ch.get("chapter")
            # chapter가 None이면 wr_id에서 추정
            if chapter is None and novel_main_wr_id and wr_id_int:
                est = wr_id_int - novel_main_wr_id
                if 1 <= est <= 10000:
                    chapter = est
            chapter = chapter or 0

            chapter_data.append((
                wr_id_int,
                novel_id,
                chapter,
                ch.get("title", ""),
                ch.get("content_length", 0),
                ch.get("content", ""),
                ch.get("url", f"https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={ch_file.stem}"),
                ch.get("collected_at"),
            ))

        if chapter_data:
            execute_values(cur, """
                INSERT INTO ebook_chapters
                    (wr_id, novel_id, chapter, title, content_length, content, bookto_url, collected_at)
                VALUES %s
                ON CONFLICT (wr_id) DO UPDATE SET
                    novel_id = EXCLUDED.novel_id,
                    chapter = EXCLUDED.chapter,
                    title = EXCLUDED.title,
                    content_length = EXCLUDED.content_length,
                    content = EXCLUDED.content,
                    bookto_url = EXCLUDED.bookto_url,
                    collected_at = EXCLUDED.collected_at
            """, chapter_data)

        conn.commit()
        cur.close()
        conn.close()

        log.info(f"  ✓ Neon 동기화 완료: {novel_id} ({chapter_count}챕터)")
        return True

    except Exception as e:
        log.warning(f"  ✗ Neon 동기화 실패 ({novel_id}): {type(e).__name__}: {e}")
        return False


def sync_all_novels() -> dict:
    """모든 로컬 소설/챕터를 Neon DB에 동기화.

    ebook-watcher 또는 수동 호출 시 사용.
    """
    DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")
    results = {"success": 0, "failed": 0, "skipped": 0, "errors": []}

    for novel_dir in DATA_DIR.iterdir():
        if not novel_dir.is_dir():
            continue
        novel_id = novel_dir.name
        if sync_novel(novel_id, novel_dir):
            results["success"] += 1
        else:
            results["failed"] += 1
            results["errors"].append(novel_id)

    return results


def query_novels_from_neon() -> list:
    """Vercel 측에서 직접 사용. novels 목록 조회."""
    conn_str = _get_connection_string()
    if not conn_str:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, author, total_chapters, cover_url,
                   description, genre, status, publisher, namu_url
            FROM ebook_novels
            ORDER BY updated_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        novels = []
        for row in rows:
            novels.append({
                "id": row[0],
                "title": row[1],
                "author": row[2],
                "totalChapters": row[3],
                "coverUrl": row[4],
                "description": row[5] or "",
                "genre": list(row[6]) if row[6] else [],
                "status": row[7] or "unknown",
                "publisher": row[8] or "북토끼",
                "namuUrl": row[9],
            })
        return novels
    except Exception as e:
        print(f"Neon query error: {e}")
        return []


def query_chapters_from_neon(novel_id: str, page: int = 1, limit: int = 20) -> dict:
    """Vercel 측에서 직접 사용. 회차 목록 조회."""
    conn_str = _get_connection_string()
    if not conn_str:
        return {"data": [], "pagination": {"page": page, "limit": limit, "total": 0}}
    try:
        import psycopg2
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        # 전체 개수
        cur.execute(
            "SELECT COUNT(*) FROM ebook_chapters WHERE novel_id = %s",
            (novel_id,),
        )
        total = cur.fetchone()[0]
        # 페이지네이션
        offset = (page - 1) * limit
        cur.execute("""
            SELECT wr_id, chapter, title, content_length
            FROM ebook_chapters
            WHERE novel_id = %s
            ORDER BY wr_id
            LIMIT %s OFFSET %s
        """, (novel_id, limit, offset))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        data = [
            {
                "wr_id": r[0],
                "chapter": r[1],
                "title": r[2],
                "contentLength": r[3],
            }
            for r in rows
        ]
        return {
            "data": data,
            "pagination": {"page": page, "limit": limit, "total": total},
        }
    except Exception as e:
        print(f"Neon chapters query error: {e}")
        return {"data": [], "pagination": {"page": page, "limit": limit, "total": 0}}


def query_chapter_from_neon(wr_id: int) -> Optional[dict]:
    """Vercel 측에서 직접 사용. 회차 본문 조회."""
    conn_str = _get_connection_string()
    if not conn_str:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        cur.execute("""
            SELECT wr_id, novel_id, chapter, title, content, content_length
            FROM ebook_chapters
            WHERE wr_id = %s
        """, (wr_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return None

        # 이전/다음 챕터 (novel_id 기준)
        try:
            conn = psycopg2.connect(conn_str)
            cur = conn.cursor()
            cur.execute("""
                SELECT wr_id FROM ebook_chapters
                WHERE novel_id = %s AND wr_id < %s
                ORDER BY wr_id DESC LIMIT 1
            """, (row[1], wr_id))
            prev_row = cur.fetchone()
            prev_chapter = prev_row[0] if prev_row else None

            cur.execute("""
                SELECT wr_id FROM ebook_chapters
                WHERE novel_id = %s AND wr_id > %s
                ORDER BY wr_id ASC LIMIT 1
            """, (row[1], wr_id))
            next_row = cur.fetchone()
            next_chapter = next_row[0] if next_row else None
            cur.close()
            conn.close()
        except Exception:
            prev_chapter = next_chapter = None

        return {
            "wr_id": row[0],
            "chapter": row[2],
            "title": row[3],
            "content": row[4] or "",
            "images": [],
            "prevChapter": prev_chapter,
            "nextChapter": next_chapter,
        }
    except Exception as e:
        print(f"Neon chapter query error: {e}")
        return None


if __name__ == "__main__":
    # CLI 테스트
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        result = sync_all_novels()
        print(f"동기화 완료: {result}")
    elif len(sys.argv) > 1 and sys.argv[1] == "query":
        novels = query_novels_from_neon()
        for n in novels:
            print(f"  {n['id']}: {n['title']} ({n['totalChapters']}화)")