#!/usr/bin/env python3
# Status: experimental
# Path: 사용자 직접 실행
"""수집된 웹소설 JSON → EPUB 변환기.

ebooklib을 사용하여 회차별 목차, 커버, 메타데이터를 포함한 EPUB3 생성.
4종류 폰트(NotoSansKR, RIDIBatang, MaruBuri, Literata) 임베딩 지원.
"""

import json
import re
import sys
from pathlib import Path

from ebooklib import epub

NOVEL_DIR = Path("/opt/ai_data/flaresolverr/novels/하남자의_탑_공략법")
COVER_IMAGE = Path("/tmp/cover.jpg")
OUTPUT_DIR = Path("/opt/workspace/ebooklib/output")
FIRST_WR_ID = 21431
TITLE = "하남자의 탑 공략법"
AUTHOR = "미상"

FONTS = {
    "NotoSansKR": Path("/tmp/fonts/NotoSansKR-Regular.ttf"),
    "RIDIBatang": Path("/tmp/fonts/RIDIBatang.otf"),
    "MaruBuri": Path("/tmp/fonts/TTF/MaruBuri-Regular.ttf"),
    "Literata": Path("/tmp/fonts/Literata-Variable.ttf"),
}


def load_episodes():
    files = sorted(NOVEL_DIR.glob("*.json"), key=lambda f: int(f.stem))
    episodes = []
    for f in files:
        data = json.load(open(f))
        if data.get("content_length", 0) > 0:
            episodes.append(data)
    return episodes


def build_css():
    return """
@font-face {
    font-family: 'NotoSansKR';
    src: url('fonts/NotoSansKR-Regular.ttf') format('truetype');
    font-weight: normal;
}
@font-face {
    font-family: 'RIDIBatang';
    src: url('fonts/RIDIBatang.otf') format('opentype');
    font-weight: normal;
}
@font-face {
    font-family: 'MaruBuri';
    src: url('fonts/MaruBuri-Regular.ttf') format('truetype');
    font-weight: normal;
}
@font-face {
    font-family: 'Literata';
    src: url('fonts/Literata-Variable.ttf') format('truetype');
    font-weight: normal;
}

body {
    font-family: 'NotoSansKR', 'RIDIBatang', serif;
    font-size: 1em;
    line-height: 1.8;
    color: #333;
    margin: 0;
    padding: 0 1em;
}
h1 {
    font-size: 1.5em;
    text-align: center;
    margin: 2em 0 1em;
    color: #222;
}
p {
    text-indent: 1em;
    margin: 0.3em 0;
}
p.no-indent {
    text-indent: 0;
}
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 2em 0;
}
"""


def build_cover_html():
    return """
<html xmlns="http://www.w3.org/1999/xhtml">
<head><style>
body {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    margin: 0;
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'NotoSansKR', serif;
}
.cover-inner {
    text-align: center;
}
h1 {
    font-size: 2.5em;
    margin: 0.5em 0;
    color: #fff;
}
.author {
    font-size: 1.2em;
    color: #aaa;
}
</style></head>
<body>
<div class="cover-inner">
    <h1>하남자의 탑 공략법</h1>
    <p class="author">작가 미상</p>
</div>
</body>
</html>
"""


def build_title_page():
    return """
<html xmlns="http://www.w3.org/1999/xhtml">
<head><style>
body {
    font-family: 'NotoSansKR', serif;
    text-align: center;
    padding-top: 30%;
}
h1 { font-size: 2em; }
p { color: #666; }
</style></head>
<body>
<h1>하남자의 탑 공략법</h1>
<p>작가 미상</p>
<hr/>
<p>총 557화</p>
</body>
</html>
"""


def build_chapter_html(episode):
    title = episode["title"]
    content = episode["content"]

    paragraphs = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+화$", line):
            paragraphs.append(f'<p class="no-indent"><strong>{line}</strong></p>')
        else:
            paragraphs.append(f"<p>{line}</p>")

    body = "\n".join(paragraphs)

    return f"""
<html xmlns="http://www.w3.org/1999/xhtml">
<head><style>
body {{
    font-family: 'NotoSansKR', 'RIDIBatang', serif;
    font-size: 1em;
    line-height: 1.8;
    color: #333;
    padding: 0 1.5em;
}}
h1 {{
    font-size: 1.4em;
    text-align: center;
    margin: 2em 0 1em;
    color: #222;
}}
p {{
    text-indent: 1em;
    margin: 0.3em 0;
}}
p.no-indent {{
    text-indent: 0;
}}
</style></head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""


def create_epub(episodes, output_name=None):
    if not episodes:
        print("수집된 회차가 없습니다.")
        return

    if not output_name:
        output_name = f"{TITLE}.epub"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / output_name

    book = epub.EpubBook()
    book.set_identifier("devforge-novel-hanamja-tower")
    book.set_title(TITLE)
    book.set_language("ko")
    book.add_author(AUTHOR)
    book.add_metadata("DC", "description", "보신주의, 안전 제일주의, 하남자 소시민이 탑을 올라갑니다.")
    book.add_metadata("DC", "publisher", "DevForge Archive")

    # CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=build_css().encode("utf-8"),
    )
    book.add_item(css)

    # 폰트들 임베딩
    font_uids = {
        "NotoSansKR": "font_noto",
        "RIDIBatang": "font_ridi",
        "MaruBuri": "font_maru",
        "Literata": "font_literata",
    }
    font_mimes = {
        "NotoSansKR": "font/ttf",
        "RIDIBatang": "font/otf",
        "MaruBuri": "font/ttf",
        "Literata": "font/truetype",
    }
    for name, path in FONTS.items():
        if path.exists():
            font = epub.EpubItem(
                uid=font_uids[name],
                file_name=f"fonts/{path.name}",
                media_type=font_mimes[name],
                content=path.read_bytes(),
            )
            book.add_item(font)
            print(f"  폰트 임베딩: {name} ({path.stat().st_size/1024/1024:.1f}MB)")

    # 커버 이미지
    if COVER_IMAGE.exists():
        with open(COVER_IMAGE, "rb") as f:
            cover_data = f.read()
        book.set_cover("cover.jpg", cover_data)
        print(f"  커버 이미지: {COVER_IMAGE.name} ({len(cover_data)/1024:.0f}KB)")
    else:
        # 이미지 없으면 HTML 커버
        cover = epub.EpubHtml(title="표지", file_name="cover.xhtml", lang="ko")
        cover.content = build_cover_html().encode("utf-8")
        cover.add_item(css)
        book.add_item(cover)

    # 목차 페이지
    title_page = epub.EpubHtml(title="책 정보", file_name="title.xhtml", lang="ko")
    title_page.content = build_title_page().encode("utf-8")
    title_page.add_item(css)
    book.add_item(title_page)

    # 회차
    chapters = []
    for ep in episodes:
        ch = epub.EpubHtml(
            title=ep["title"],
            file_name=f'chapter_{ep["wr_id"]}.xhtml',
            lang="ko",
        )
        ch.content = build_chapter_html(ep).encode("utf-8")
        ch.add_item(css)
        book.add_item(ch)
        chapters.append(ch)

    # 목차
    toc = [
        epub.Link("title.xhtml", "책 정보", "title_page"),
    ]
    for ch in chapters:
        toc.append(epub.Link(ch.file_name, ch.title, ch.title))

    book.toc = toc

    # spine
    book.spine = ["nav", title_page] + chapters

    # NCX, Nav
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(out_path), book)

    size_kb = out_path.stat().st_size / 1024
    print(f"\nEPUB 생성 완료: {out_path}")
    print(f"  회차: {len(chapters)}화")
    print(f"  크기: {size_kb:.0f}KB ({size_kb/1024:.1f}MB)")
    return out_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="웹소설 JSON → EPUB 변환")
    parser.add_argument("--start", type=int, help="시작 회차 번호")
    parser.add_argument("--end", type=int, help="끝 회차 번호")
    parser.add_argument("--output", "-o", help="출력 파일명")
    parser.add_argument("--dry-run", action="store_true", help="변환 가능한 회차 확인만")
    args = parser.parse_args()

    episodes = load_episodes()
    print(f"수집된 회차: {len(episodes)}화")

    if args.start:
        episodes = [e for e in episodes if e.get("chapter", 0) >= args.start]
    if args.end:
        episodes = [e for e in episodes if e.get("chapter", 0) <= args.end]

    print(f"변환 대상: {len(episodes)}화")

    if args.dry_run:
        for ep in episodes[:10]:
            print(f"  {ep['title']} ({ep['content_length']}자)")
        if len(episodes) > 10:
            print(f"  ... 외 {len(episodes)-10}화")
        return

    create_epub(episodes, output_name=args.output)


if __name__ == "__main__":
    main()
