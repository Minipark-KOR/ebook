#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/storage.py
"""챕터 저장/메타데이터 관리 — 모든 수집기 공용.

ebook_worker.py의 save_chapter()와 enrich_metadata_from_namu()를 분리.
bookto31/toki31 양쪽에서 공유 가능.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


NOVELS_DIR = Path("/opt/ai_data/flaresolverr/novels")
COVERS_DIR = Path("/opt/ai_data/flaresolverr/covers")


def get_novel_dir(novel_title: str) -> Path:
    """소설명 → 디렉토리 경로."""
    novel_id = novel_title.replace(" ", "_").replace("/", "_") if novel_title else "unknown"
    return NOVELS_DIR / novel_id


def _extract_chapter_num(body: str) -> Optional[int]:
    """본문 첫 줄에서 회차 번호 추출.

    본문이 '1화\n...' 형태일 때 첫 줄에서 추출.
    본문이 '「레벨:...' 등으로 시작해 첫 줄이 아닌 위치에 'N화'가 있을 땐
    첫 번째로 등장하는 'N화/편/장'도 시도한다.
    """
    if not body:
        return None
    first_line = body.split("\n")[0]
    m = re.match(r"^(\d+)(?:화|편|장)", first_line)
    if m:
        return int(m.group(1))
    # 첫 줄 실패 시 본문에서 'N화/편/장' 형태를 한 번 더 찾아봄
    m = re.search(r"^(\d+)(?:화|편|장)", body.strip(), re.MULTILINE)
    return int(m.group(1)) if m else None


def save_chapter(
    novel_title: str,
    wr_id: int,
    body: str,
    source: str = "bookto31",
    chapter_num: Optional[int] = None,
) -> bool:
    """챕터 본문을 JSON 파일로 저장 + meta.json 갱신.

    Args:
        novel_title: 소설 제목
        wr_id: 북토끼/뉴토끼 wr_id
        body: 챕터 본문 텍스트
        source: 수집 소스 ("bookto31" | "toki31")
        chapter_num: 회차 번호 (None이면 본문에서 추출)

    Returns:
        성공 시 True
    """
    if chapter_num is None:
        chapter_num = _extract_chapter_num(body)
    if chapter_num is None:
        # wr_id로 폴백하면 회차 번호가 오염되어 '1화→2화' 탐색이 깨진다.
        # 정확한 번호를 모르면 chapter를 남기지 않고, 상위 계층(동기화 등)에서
        # wr_id 기반으로 추정하도록 None을 유지한다.
        chapter_num = None

    novel_id = novel_title.replace(" ", "_").replace("/", "_") if novel_title else f"novel_{wr_id}"
    novel_dir = NOVELS_DIR / novel_id
    novel_dir.mkdir(parents=True, exist_ok=True)

    # 챕터 파일 저장
    chapter_file = novel_dir / f"{wr_id}.json"
    if chapter_file.exists():
        with open(chapter_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    data.update({
        "wr_id": wr_id,
        "chapter": chapter_num,
        "title": f"{novel_title} - {chapter_num}화" if chapter_num else novel_title,
        "content_length": len(body),
        "content": body,
        "url": f"https://{source}.com/bbs/board.php?bo_table=novel&wr_id={wr_id}",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    })

    try:
        with open(chapter_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, IOError):
        return False

    # meta.json 갱신
    _update_meta(novel_dir, novel_id, novel_title)

    # 챕터 인덱스 캐시 갱신 (API 성능 최적화)
    try:
        from services.data import rebuild_chapters_index
        rebuild_chapters_index(novel_dir)
    except Exception:
        pass  # 인덱스 캐시 실패는 저장 실패와 무관

    return True


def _update_meta(novel_dir: Path, novel_id: str, novel_title: str) -> None:
    """meta.json 생성/업데이트."""
    meta_file = novel_dir / "meta.json"

    try:
        if not meta_file.exists():
            meta = {
                "id": novel_id,
                "title": novel_title,
                "author": "미상",
                "totalChapters": 1,
                "coverUrl": None,
                "description": "",
                "genre": [],
                "status": "unknown",
                "publisher": "북토끼",
                "namuUrl": None,
            }
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        else:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            chapter_files = list(novel_dir.glob("*.json"))
            chapter_count = sum(1 for f in chapter_files if f.stem.isdigit())
            if meta.get("totalChapters", 0) < chapter_count:
                meta["totalChapters"] = chapter_count
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def update_meta_from_namu(novel_title: str, namu_meta: dict) -> bool:
    """namu.wiki 메타데이터로 meta.json 업데이트.

    Args:
        novel_title: 소설 제목
        namu_meta: metadata_namu.get_metadata() 반환 dict

    Returns:
        성공 시 True
    """
    novel_id = novel_title.replace(" ", "_").replace("/", "_")
    novel_dir = NOVELS_DIR / novel_id
    meta_file = novel_dir / "meta.json"

    if not meta_file.exists():
        return False

    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        if namu_meta.get("author"):
            meta["author"] = namu_meta["author"]
        if namu_meta.get("cover_url"):
            meta["coverUrl"] = namu_meta["cover_url"]
        if namu_meta.get("description"):
            meta["description"] = namu_meta["description"]
        if namu_meta.get("genre"):
            meta["genre"] = namu_meta["genre"]
        # status는 namu가 아닌 discover(소스 기반)가 결정하므로 덮어쓰지 않는다.
        # namu의 연재상태는 수동 편집이라 stale/부정확 (완결인데 수집 중 등).
        if namu_meta.get("publisher"):
            meta["publisher"] = namu_meta["publisher"]
        if namu_meta.get("url"):
            meta["namuUrl"] = namu_meta["url"]

        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
