#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/epub.py
"""EPUB 생성 서비스.

DB에 저장된 챕터 JSON 파일들을 모아서 EPUB 파일을 생성한다.
한글 텍스트라 UTF-8 인코딩 필수. GoNoto 폰트 임베드로 한글 깨짐 방지.
"""

import io
import json
import re
from pathlib import Path
from typing import Optional

from ebooklib import epub


DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")
FONT_PATH = Path("/home/opc/.local/lib/python3.11/site-packages/static/fonts/GoNotoCurrent-Regular.ttf")


def _read_chapter(chapter_file: Path) -> Optional[dict]:
    """챕터 JSON 파일 읽기."""
    try:
        with open(chapter_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clean_content(content: str) -> str:
    """본문에서 불필요한 패턴 제거, 문단 분리."""
    if not content:
        return ""
    lines = content.split("\n")
    if lines and re.match(r"^\s*\d+화", lines[0]):
        lines = lines[1:]
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _build_chapter_html(title: str, content: str) -> bytes:
    """EPUB 챕터 HTML 바이트 생성 (CSS로 한글 폰트 적용)."""
    css = (
        "@font-face {"
        '  font-family: "GoNotoCurrent";'
        '  src: url("../fonts/GoNotoCurrent-Regular.ttf") format("truetype");'
        "}\n"
        "body {"
        '  font-family: "GoNotoCurrent", "Noto Sans", sans-serif;'
        "  line-height: 1.7;"
        "  margin: 1em;"
        "}\n"
        "h1 {"
        "  font-size: 1.4em;"
        "  margin-bottom: 1em;"
        "  page-break-before: always;"
        "}\n"
        "p {"
        "  margin: 0.5em 0;"
        "  text-indent: 1em;"
        "}"
    )

    body_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE html>',
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">',
        "<head>",
        f"<title>{title}</title>",
        f"<style>{css}</style>",
        "</head>",
        "<body>",
        f"<h1>{title}</h1>",
    ]
    for para in content.split("\n"):
        if para.strip():
            body_parts.append(f"<p>{para.strip()}</p>")
    body_parts.extend(["</body>", "</html>"])
    return "\n".join(body_parts).encode("utf-8")


def build_epub(novel_id: str) -> Optional[bytes]:
    """EPUB 바이트 생성.

    Args:
        novel_id: 소설 디렉토리명 (예: "하남자의_탑_공략법")

    Returns:
        EPUB 파일 바이트. 실패 시 None.
    """
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.is_dir():
        return None

    chapter_files = sorted(
        [f for f in novel_dir.iterdir() if f.suffix == ".json" and f.stem.isdigit()],
        key=lambda f: int(f.stem),
    )
    if not chapter_files:
        return None

    meta = {}
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

    first_chapter = _read_chapter(chapter_files[0]) or {}
    title = meta.get("title") or first_chapter.get("title", novel_id).split(" - ")[0]
    author = meta.get("author", "미상")
    description = meta.get("description", "")

    book = epub.EpubBook()
    book.set_identifier(f"ebooklib-{novel_id}")
    book.set_title(title)
    book.set_language("ko")
    book.add_author(author)
    if description:
        book.add_metadata("DC", "description", description)

    # 폰트 임베드 (한글 표시)
    if FONT_PATH.exists():
        with open(FONT_PATH, "rb") as f:
            font_content = f.read()
        font_item = epub.EpubItem(
            uid="font_gonoto",
            file_name="fonts/GoNotoCurrent-Regular.ttf",
            media_type="application/font-sfnt",
            content=font_content,
        )
        book.add_item(font_item)

    chapter_items = []
    for idx, chap_file in enumerate(chapter_files, 1):
        ch = _read_chapter(chap_file)
        if not ch:
            continue
        ch_title = ch.get("title") or f"챕터 {idx}"
        ch_content = _clean_content(ch.get("content", ""))

        chapter = epub.EpubHtml(
            uid=f"chap_{idx:04d}",
            title=ch_title,
            file_name=f"chap_{idx:04d}.xhtml",
            lang="ko",
            content=_build_chapter_html(ch_title, ch_content),
        )
        book.add_item(chapter)
        chapter_items.append(chapter)

    if not chapter_items:
        return None

    book.toc = tuple(chapter_items)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *chapter_items]

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


def get_novel_title(novel_id: str) -> str:
    """소설 제목 조회."""
    novel_dir = DATA_DIR / novel_id
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f).get("title", novel_id)
    return novel_id