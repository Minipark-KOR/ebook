#!/usr/bin/env python3
# Status: experimental
# Path: ebooklib/apps/backend/services/epub.py
"""EPUB 생성 서비스.

DB에 저장된 챕터 JSON 파일들을 모아서 EPUB 파일을 생성한다.
한글 텍스트라 UTF-8 인코딩 필수. 4개 폰트 임베드 (한글 + 영문).

폰트 시스템:
- NotoSansKR: 한글 고딕 (제목, h1)
- RIDIBatang: 한글 세리프 (본문)
- MaruBuri: 한글 둥근고딕 (인용)
- Literata: 영문 세리프 (fallback)
"""

import io
import json
import re
from pathlib import Path
from typing import Optional, Dict

from ebooklib.epub import (
    EpubBook,
    EpubHtml,
    EpubNcx,
    EpubNav,
    EpubItem,
    write_epub,
)


DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")
FONTS_DIR = Path("/opt/workspace/ebooklib/scripts/fonts")
COVERS_DIR = Path("/opt/ai_data/flaresolverr/covers")

# 4개 폰트 정의 (filename, font-family name, MIME type)
FONTS = [
    ("NotoSansKR-Regular.ttf", "NotoSansKR", "application/font-sfnt"),
    ("RIDIBatang.otf", "RIDIBatang", "application/vnd.ms-opentype"),
    ("MaruBuri-Regular.ttf", "MaruBuri", "application/font-sfnt"),
    ("Literata-Variable.ttf", "Literata", "application/font-sfnt"),
]


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
    """EPUB 챕터 HTML 바이트 생성."""
    body_parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE html>',
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">',
        "<head>",
        f"<title>{title}</title>",
        '<link rel="stylesheet" type="text/css" href="../styles/main.css" />',
        "</head>",
        "<body>",
        f"<h1>{title}</h1>",
    ]
    for para in content.split("\n"):
        if para.strip():
            body_parts.append(f"<p>{para.strip()}</p>")
    body_parts.extend(["</body>", "</html>"])
    return "\n".join(body_parts).encode("utf-8")


def _build_main_css() -> bytes:
    """다중 폰트 + 스타일 메인 CSS."""
    font_face_rules = "\n".join([
        f"""@font-face {{
  font-family: "{family}";
  font-weight: 400;
  font-style: normal;
  src: url("../fonts/{filename}") format("{'opentype' if filename.endswith('.otf') else 'truetype'}");
}}"""
        for filename, family, _ in FONTS
    ])

    css = f"""
{font_face_rules}

body {{
  font-family: "RIDIBatang", "NotoSansKR", "Literata", serif;
  line-height: 1.8;
  margin: 1.5em;
  font-size: 1em;
  color: #222;
  background-color: #fefefe;
}}

h1 {{
  font-family: "NotoSansKR", "Literata", sans-serif;
  font-size: 1.6em;
  font-weight: 700;
  margin-top: 0;
  margin-bottom: 1.5em;
  padding-bottom: 0.5em;
  border-bottom: 2px solid #444;
  page-break-before: always;
}}

h2 {{
  font-family: "NotoSansKR", sans-serif;
  font-size: 1.3em;
  font-weight: 600;
  margin: 1em 0 0.6em;
  color: #333;
}}

h3 {{
  font-family: "NotoSansKR", sans-serif;
  font-size: 1.1em;
  font-weight: 600;
  margin: 1em 0 0.5em;
  color: #444;
}}

p {{
  margin: 0.8em 0;
  text-indent: 1em;
  line-height: 1.8;
  word-break: keep-all;
}}

blockquote {{
  font-family: "MaruBuri", "NotoSansKR", sans-serif;
  margin: 1em 2em;
  padding: 0.5em 1em;
  border-left: 3px solid #888;
  color: #555;
  background-color: #f8f8f8;
}}

em, i {{
  font-style: italic;
}}

strong, b {{
  font-weight: 700;
}}
"""
    return css.encode("utf-8")


def _add_fonts_and_css(book: "EpubBook") -> None:
    """EPUB에 4개 폰트 + 메인 CSS 임베드."""
    # 메인 CSS
    css_item = EpubItem(
        uid="main_styles",
        file_name="styles/main.css",
        media_type="text/css",
        content=_build_main_css(),
    )
    book.add_item(css_item)

    # 폰트들
    for idx, (filename, family, mime_type) in enumerate(FONTS):
        font_path = FONTS_DIR / filename
        if not font_path.exists():
            continue
        with open(font_path, "rb") as f:
            font_content = f.read()
        font_item = EpubItem(
            uid=f"font_{idx}_{family.lower()}",
            file_name=f"fonts/{filename}",
            media_type=mime_type,
            content=font_content,
        )
        book.add_item(font_item)


def _get_cover_path(novel_id: str) -> Optional[Path]:
    """소설 표지 이미지 경로 찾기 (covers/{novel_id}.{확장자})."""
    for ext in (".webp", ".jpg", ".jpeg", ".png"):
        p = COVERS_DIR / f"{novel_id}{ext}"
        if p.exists():
            return p
    return None


def _build_cover_html(title: str, cover_href: str) -> bytes:
    """EPUB 표지 페이지 HTML."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head>"
        f"<title>{title}</title>"
        "</head>"
        "<body>"
        '<div style="text-align:center; margin:0 auto; padding:2em 0;">'
        f'<img src="{cover_href}" alt="{title}" style="max-width:100%; height:auto; box-shadow:0 2px 8px rgba(0,0,0,.3);" />'
        f"<h2 style=\"font-size:1.4em; margin-top:1em;\">{title}</h2>"
        "</div>"
        "</body></html>"
    )
    return body.encode("utf-8")


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

    # 메타데이터
    meta: Dict = {}
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

    first_chapter = _read_chapter(chapter_files[0]) or {}
    title = meta.get("title") or first_chapter.get("title", novel_id).split(" - ")[0]
    author = meta.get("author", "미상")
    description = meta.get("description", "")
    publisher = meta.get("publisher", "북토끼")
    language = meta.get("language", "ko")

    book = EpubBook()
    book.set_identifier(f"ebooklib-{novel_id}")
    book.set_title(title)
    book.set_language(language)
    book.add_author(author)
    if publisher:
        book.add_metadata("DC", "publisher", publisher)
    if description:
        book.add_metadata("DC", "description", description)

    # 4개 폰트 + CSS 임베드
    _add_fonts_and_css(book)

    # 표지 이미지 임베드
    cover_page = None
    cover_path = _get_cover_path(novel_id)
    if cover_path:
        ext = cover_path.suffix.lower()
        mime = {
            ".webp": "image/webp",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }.get(ext, "image/webp")
        with open(cover_path, "rb") as f:
            cover_bytes = f.read()
        book.set_cover(f"cover{ext}", cover_bytes, create_page=False)
        cover_page = EpubHtml(
            uid="cover",
            title="표지",
            file_name="cover.xhtml",
            lang=language,
            content=_build_cover_html(title, f"cover{ext}"),
        )
        book.add_item(cover_page)

    # 챕터 변환
    chapter_items = []
    for idx, chap_file in enumerate(chapter_files, 1):
        ch = _read_chapter(chap_file)
        if not ch:
            continue
        ch_title = ch.get("title") or f"챕터 {idx}"
        ch_content = _clean_content(ch.get("content", ""))

        chapter = EpubHtml(
            uid=f"chap_{idx:04d}",
            title=ch_title,
            file_name=f"chap_{idx:04d}.xhtml",
            lang=language,
            content=_build_chapter_html(ch_title, ch_content),
        )
        book.add_item(chapter)
        chapter_items.append(chapter)

    if not chapter_items:
        return None

    # 목차 / 네비게이션
    book.toc = tuple(chapter_items)
    book.add_item(EpubNcx())
    book.add_item(EpubNav())
    book.spine = (["cover"] if cover_page else []) + ["nav", *chapter_items]

    buf = io.BytesIO()
    write_epub(buf, book)
    return buf.getvalue()


def get_novel_title(novel_id: str) -> str:
    """소설 제목 조회."""
    novel_dir = DATA_DIR / novel_id
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f).get("title", novel_id)
    return novel_id