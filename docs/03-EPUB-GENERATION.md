# EPUB 생성 시스템

> 챕터 본문들을 모아서 한글 깨짐 없는 EPUB 파일을 만드는 시스템.

## 개요

ebooklib의 EPUB 생성은 **로컬 DB의 챕터 JSON 파일들을 모아서** 하나의 EPUB 파일로 묶고, **한글 폰트를 임베드**해서 어디서나 읽을 수 있게 합니다.

### 출력
- 형식: EPUB 3.0
- 크기: ~9MB (557챕터 + 4개 한글 폰트 ~11MB 임베드)
- 시간: 약 2.3초
- 다운로드: `/api/novels/{id}/epub`

## 아키텍처

```
[요청] GET /api/novels/{소설ID}/epub
   ↓
[라우터] routers/novels.py - download_epub()
   ├─ get_novel_detail() (DB 검증)
   └─ build_epub(novel_id)  ← services/epub.py
        ├─ DATA_DIR/{소설ID}/*.json glob
        ├─ 각 JSON 파싱 → EpubHtml 변환
        ├─ 4개 한글 폰트 임베드 (NotoSansKR/RIDIBatang/MaruBuri/Literata)
        └─ EPUB 바이트 반환
   ↓
[응답] Content-Type: application/epub+zip
       Content-Disposition: attachment; filename*=UTF-8''{제목}.epub
```

## 코드 구조

### routers/novels.py
```python
@router.get("/novels/{novel_id}/epub")
async def download_epub(novel_id: str):
    novel = get_novel_detail(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    epub_bytes = build_epub(novel_id)
    if not epub_bytes:
        raise HTTPException(status_code=500, detail="EPUB 생성 실패")

    title = get_novel_title(novel_id)
    filename = f"{title}.epub"

    return Response(
        content=epub_bytes,
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(len(epub_bytes)),
        },
    )
```

### services/epub.py

```python
from ebooklib.epub import EpubBook, EpubHtml, EpubNcx, EpubNav, EpubItem, write_epub

FONTS_DIR = Path("/opt/workspace/ebooklib/scripts/fonts")
DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")

# 4개 폰트 정의
FONTS = [
    ("NotoSansKR-Regular.ttf", "NotoSansKR", "application/font-sfnt"),
    ("RIDIBatang.otf", "RIDIBatang", "application/vnd.ms-opentype"),
    ("MaruBuri-Regular.ttf", "MaruBuri", "application/font-sfnt"),
    ("Literata-Variable.ttf", "Literata", "application/font-sfnt"),
]


def build_epub(novel_id: str) -> Optional[bytes]:
    """EPUB 바이트 생성."""
    novel_dir = DATA_DIR / novel_id
    if not novel_dir.is_dir():
        return None

    chapter_files = sorted(
        [f for f in novel_dir.iterdir() if f.suffix == ".json" and f.stem.isdigit()],
        key=lambda f: int(f.stem),
    )
    if not chapter_files:
        return None

    # 메타데이터 (meta.json 우선)
    meta = {}
    meta_file = novel_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file) as f:
            meta = json.load(f)

    first_chapter = _read_chapter(chapter_files[0]) or {}
    title = meta.get("title") or first_chapter.get("title", novel_id).split(" - ")[0]
    author = meta.get("author", "미상")

    book = EpubBook()
    book.set_identifier(f"ebooklib-{novel_id}")
    book.set_title(title)
    book.set_language("ko")
    book.add_author(author)

    # 4개 폰트 + CSS 임베드
    _add_fonts_and_css(book)

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
            lang="ko",
            content=_build_chapter_html(ch_title, ch_content),
        )
        book.add_item(chapter)
        chapter_items.append(chapter)

    if not chapter_items:
        return None

    book.toc = tuple(chapter_items)
    book.add_item(EpubNcx())
    book.add_item(EpubNav())
    book.spine = ["nav", *chapter_items]

    buf = io.BytesIO()
    write_epub(buf, book)
    return buf.getvalue()
```

### 챕터 HTML (CSS로 한글 폰트 적용)

```python
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
```

## 한글 폰트 임베드

### 왜 임베드?
- EPUB 리더가 한글을 표시하려면 해당 폰트가 있어야 함
- 사용자 디바이스에 없으면 □ 박스로 깨짐
- **4개 폰트 동시 임베드**:
  - NotoSansKR (고딕, 제목용)
  - RIDIBatang (세리프, 본문용)
  - MaruBuri (둥근고딕, 인용용)
  - Literata (영문 세리프, fallback)
- 폰트 위치: `/opt/workspace/ebooklib/scripts/fonts/`

### CSS @font-face
```css
@font-face {
  font-family: "RIDIBatang";
  src: url("../fonts/RIDIBatang.otf") format("opentype");
}
@font-face {
  font-family: "NotoSansKR";
  src: url("../fonts/NotoSansKR-Regular.ttf") format("truetype");
}
body {
  font-family: "RIDIBatang", "NotoSansKR", "Literata", serif;
  line-height: 1.8;
}
h1 {
  font-family: "NotoSansKR", "Literata", sans-serif;
}
blockquote {
  font-family: "MaruBuri", "NotoSansKR", sans-serif;
}
```
```

## EPUB 구조

```
{title}.epub (zip)
├── mimetype                          # application/epub+zip
├── META-INF/
│   └── container.xml                 # EPUB 패키지 정의
└── EPUB/
    ├── content.opf                   # 패키지 매니페스트
    ├── nav.xhtml                      # 탐색 (목차)
    ├── ncx.xml                        # EPUB 2 호환용 목차
    ├── cover.webp                     # 표지 이미지 (16KB)
    ├── cover.xhtml                    # 표지 페이지 (spine 첫 번째)
    ├── fonts/
    │   ├── NotoSansKR-Regular.ttf      # 한글 고딕 (6MB)
    │   ├── RIDIBatang.otf              # 한글 세리프 (1.4MB)
    │   ├── MaruBuri-Regular.ttf        # 한글 둥근고딕 (3.1MB)
    │   └── Literata-Variable.ttf       # 영문 세리프 (167KB)
    ├── chap_0001.xhtml                # 1화
    ├── chap_0002.xhtml                # 2화
    ├── ...
    └── chap_0557.xhtml                # 557화
```

## 챕터 본문 전처리

### 첫 줄 제거 ("1화\n2004년...")
원본:
```
1화
2004년.
지구 곳곳에 거대한 검은 탑...
```

전처리 후:
```
2004년.
지구 곳곳에 거대한 검은 탑...
```

### 코드
```python
def _clean_content(content: str) -> str:
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
```

### 빈 챕터 감지
북토끼가 502/522 에러로 빈 데이터를 반환할 수 있음:
```python
if not ch_content or len(ch_content) < 100:
    continue  # 또는 북토끼에서 재수집
```

## 성능 최적화

### 캐싱
- 현재: 매 요청마다 EPUB 생성 (2.3초)
- 권장: Redis 또는 메모리 캐시 (1시간 TTL)

### 스트리밍
- 현재: 메모리에 전체 EPUB 빌드 (9MB, 557화 기준)
- 대안: `StreamingResponse`로 점진적 전송

## 테스트

### 수동 테스트
```bash
# CLI에서 EPUB 생성
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.')
from services.epub import build_epub
data = build_epub('하남자의_탑_공략법')
with open('/tmp/test.epub', 'wb') as f:
    f.write(data)
print(f'Size: {len(data):,} bytes')
"

# API 호출
curl -o test.epub "https://miniebook.vercel.app/api/novels/%ED%95%98%EB%82%A8%EC%9E%90%EC%9D%98_%ED%83%91_%EA%B3%B5%EB%9E%B5%EB%B2%95/epub"
file test.epub
```

### EPUB 검증
```bash
# 압축 확인
unzip -l test.epub
# → EPUB/chap_0001.xhtml, EPUB/fonts/NotoSansKR-Regular.ttf, RIDIBatang.otf 등

# 챕터 내용 일부 확인
unzip -p test.epub EPUB/chap_0001.xhtml | head -50
```

## 한계

### 크기
- 챕터당 ~8KB → 557챕터 = 4.4MB 본문
- +폰트 4개 ~11MB → **총 ~9MB**
- 더 큰 소설 (1000챕터+): 15MB+ EPUB

### 폰트 라이선스
- NotoSansKR: SIL Open Font License (OFL) - 자유 재배포 가능
- RIDIBatang: RIDI 배포용 라이선스 - EPUB 내 임베드 허용
- MaruBuri: SIL Open Font License (OFL)
- Literata: SIL Open Font License (OFL)
- EPUB 내 임베드는 모두 OFL/허용 범위 내

### 호환성
- Apple Books: ✅
- Calibre: ✅
- Kindle (via Calibre 변환): ⚠️ AZW3 변환 권장
- Google Play Books: ✅
- Kobo: ✅

## 다음 문서
- [04-API-REFERENCE.md](04-API-REFERENCE.md) - API 명세