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
import os
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
EPUB_DIR = Path("/opt/ai_data/flaresolverr/epub")

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


def _get_cover_jpeg(novel_id: str) -> Optional[Path]:
    """표지를 JPEG로 변환/캐시해 반환.

    EPUB 리더의 webp 지원이 불안정하므로 JPEG로 통일한다.
    변환 실패 시 원본이 이미 jpg/jpeg면 그대로, 아니면 None.
    """
    src = _get_cover_path(novel_id)
    if not src:
        return None
    # 이미 jpg/jpeg면 그대로 사용
    if src.suffix.lower() in (".jpg", ".jpeg"):
        return src
    # webp/png → jpeg 변환 (EPUB_DIR에 캐시)
    try:
        jpeg_path = EPUB_DIR / f"cover_{novel_id}.jpg"
        if jpeg_path.exists():
            return jpeg_path
        from PIL import Image
        img = Image.open(src)
        img = img.convert("RGB")
        img.save(jpeg_path, "JPEG", quality=90)
        return jpeg_path
    except Exception:
        return None


def _build_cover_html(title: str, author: str, cover_href: str) -> bytes:
    """EPUB 표지 페이지 HTML (이미지 포함)."""
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
        f"<p style=\"color:#666; margin-top:.2em;\">{author}</p>"
        "</div>"
        "</body></html>"
    )
    return body.encode("utf-8")


def _build_title_page_html(title: str, author: str, publisher: str, description: str) -> bytes:
    """표지 이미지가 없을 때 사용하는 텍스트 타이틀 페이지 HTML."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head>"
        f"<title>{title}</title>"
        "</head>"
        "<body>"
        '<div style="text-align:center; margin:0 auto; padding:3em 1em;">'
        f'<h1 style="font-size:1.8em; margin-bottom:.5em;">{title}</h1>'
        f"<p style=\"font-size:1.2em; color:#333; margin-top:.5em;\">{author}</p>"
    )
    if publisher:
        body += f"<p style=\"color:#666; margin-top:.3em;\">{publisher}</p>"
    if description:
        body += f'<p style="margin-top:2em; color:#555; line-height:1.8;">{description}</p>'
    body += "</div></body></html>"
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

    chapter_files = [
        f for f in novel_dir.iterdir() if f.suffix == ".json" and f.stem.isdigit()
    ]
    if not chapter_files:
        return None

    # chapter 번호 기준 정렬 (wr_id 아님) — 화산귀환(1922화가 wr_id 최소) 등
    # wr_id 순서가 회차 순서와 다른 작품 대비. chapter 없으면 wr_id 폴백.
    def _chapter_sort_key(f: Path) -> tuple:
        ch = _read_chapter(f) or {}
        c = ch.get("chapter")
        if isinstance(c, int) and c > 0:
            return (0, c)
        try:
            return (1, int(f.stem))
        except ValueError:
            return (2, 0)

    chapter_files.sort(key=_chapter_sort_key)

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

    # 표지 (JPEG로 통일) + 타이틀/표지 페이지 (이미지 없어도 항상 생성)
    cover_jpeg = _get_cover_jpeg(novel_id)
    cover_page = None
    if cover_jpeg:
        cover_bytes = cover_jpeg.read_bytes()
        # set_cover: 이미지 아이템 + 메타데이터 cover 참조 (썸네일용).
        # create_page=False → 기본 페이지 생성 안 함, 커스텀 표지 페이지만 spine에 포함
        book.set_cover("cover.jpg", cover_bytes, create_page=False)
        cover_page = EpubHtml(
            uid="cover_page",
            title="표지",
            file_name="cover.xhtml",
            lang=language,
            content=_build_cover_html(title, author, "cover.jpg"),
        )
        book.add_item(cover_page)
    else:
        # 표지 이미지가 없어도 타이틀 페이지는 항상 생성
        cover_page = EpubHtml(
            uid="cover_page",
            title="표지",
            file_name="cover.xhtml",
            lang=language,
            content=_build_title_page_html(title, author, publisher, description),
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
    
    # spine: cover_page(표지/타이틀) -> nav -> 챕터
    # (cover 이미지는 set_cover 메타데이터로만 참조, spine 별도 항목 아님)
    if cover_page:
        book.spine = ["cover_page", "nav", *chapter_items]
    else:
        book.spine = ["nav", *chapter_items]

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


def get_epub_cache_path(novel_id: str) -> Path:
    """캐시된 EPUB 파일 경로 (존재 여부와 무관)."""
    return EPUB_DIR / f"{novel_id}.epub"


def get_novel_fingerprint(novel_id: str) -> Optional[dict]:
    """소설 데이터의 현재 상태 지문.

    (챕터 수, 최고 chapter 번호, 가장 최근 collected_at)으로 구성.
    이 값이 바뀌면 EPUB 재제작이 필요함을 의미한다.
    """
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.is_dir():
        return None
    chapter_files = [
        f for f in novel_dir.iterdir()
        if f.suffix == ".json" and f.stem.isdigit()
    ]
    if not chapter_files:
        return None
    max_chapter = 0
    latest_ts = ""
    for f in chapter_files:
        ch = _read_chapter(f)
        if not ch:
            continue
        c = ch.get("chapter")
        if isinstance(c, int) and c > max_chapter:
            max_chapter = c
        ts = ch.get("collected_at", "")
        if ts and ts > latest_ts:
            latest_ts = ts
    return {
        "novel_id": novel_id,
        "count": len(chapter_files),
        "max_chapter": max_chapter,
        "latest_collected_at": latest_ts,
    }


def maybe_build_epub(novel_id: str, force: bool = False) -> Optional[Path]:
    """fingerprint가 바뀐 경우에만 EPUB 재제작. 캐시 경로 반환.

    - 전체 회차 수집이 끝난 시점(queue 비움)에 호출된다.
    - fingerprint가 캐시와 같으면(변경 없음) 재빌드 없이 기존 캐시 반환.
    - 제작 실패(챕터 부족 등) 시 None.
    """
    EPUB_DIR.mkdir(parents=True, exist_ok=True)
    fp = get_novel_fingerprint(novel_id)
    if not fp:
        return None
    epub_path = get_epub_cache_path(novel_id)
    fp_file = EPUB_DIR / f"{novel_id}.fingerprint.json"
    if not force and epub_path.exists() and fp_file.exists():
        try:
            old = json.loads(fp_file.read_text(encoding="utf-8"))
            if old == fp:
                return epub_path
        except Exception:
            pass
    data = build_epub(novel_id)
    if not data:
        return None
    tmp = epub_path.with_suffix(".epub.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, epub_path)
    fp_file.write_text(json.dumps(fp, ensure_ascii=False, indent=1), encoding="utf-8")
    return epub_path