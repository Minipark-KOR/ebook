#!/usr/bin/env python3
"""문피아/조아라 듀얼 SSOT로 메타데이터 수집.

북토끼는 본문 크롤러로만 사용. 메타데이터는 문피아/조아라에서 수집.
각 사이트에서 책 검색 + 상세 페이지 메타데이터 추출.

수집 메타데이터:
- 제목 (검증용, DB의 자체 제목 신뢰)
- 작가 (가장 신뢰할 수 있는 출처)
- 장르
- 상태 (연재중/완결/단편)
- 표지 이미지 URL
- 출판사 / 플랫폼
- 설명 (가능하면)

FlareSolverr 우회로 Cloudflare 통과.
"""

import re
import sys
import time
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote

import requests

sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')
from services import bookto31

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dual_metadata_ssot")

CACHE_DIR = Path("/opt/ai_data/flaresolverr/dual_metadata_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cached_get(url: str, ttl: int = 86400) -> Optional[str]:
    """캐시 + FlareSolverr GET."""
    cache_key = CACHE_DIR / (url.replace("/", "_").replace(":", "_")[:200] + ".html")
    if cache_key.exists():
        age = time.time() - cache_key.stat().st_mtime
        if age < ttl:
            return cache_key.read_text(encoding="utf-8", errors="ignore")

    # 1초 대기 (rate limit)
    time.sleep(1)
    html = bookto31._fetch_with_flaresolverr(url, rate_limit=False)
    if html:
        cache_key.write_text(html, encoding="utf-8", errors="ignore")
    return html


# ============================================================
# 문피아 (munpia.com)
# ============================================================

def search_munpia(title: str) -> List[str]:
    """문피아 검색. 책 상세 URL 목록."""
    # 여러 URL 패턴 시도
    candidates = [
        f"https://www.munpia.com/search/search_list.do?search_type=1&search_keyword={quote(title)}",
        f"https://www.munpia.com/search/search_list.asp?search_keyword={quote(title)}",
        f"https://www.munpia.com/search/search_list.do?search_keyword={quote(title)}",
    ]
    links = []
    for url in candidates:
        html = _cached_get(url)
        if not html:
            continue
        # 책 상세 링크 추출 (다양한 패턴)
        patterns = [
            r'href="([^"]*book_view[^"]*)"',
            r'href="([^"]*novel_view[^"]*)"',
            r'href="([^"]*work_view[^"]*)"',
            r'href="(/book/\d+[^"]*)"',
            r'href="(/novel/\d+[^"]*)"',
            r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*book[^"]*"',
        ]
        for p in patterns:
            for m in re.findall(p, html, re.DOTALL):
                full = "https://www.munpia.com" + m if m.startswith("/") else m
                if full not in links and "search" not in full:
                    links.append(full)
        if links:
            break
    return links[:5]


def get_munpia_metadata(book_url: str) -> Optional[Dict]:
    """문피아 책 상세 페이지에서 메타데이터 추출."""
    html = _cached_get(book_url)
    if not html:
        return None

    meta = {
        "source": "munpia",
        "url": book_url,
    }

    # 제목
    for pat in [
        r'<title>(.*?)</title>',
        r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"',
    ]:
        m = re.search(pat, html)
        if m:
            meta["title"] = m.group(1).replace(" - 문피아", "").replace(" | 문피아", "").strip()
            break

    # 작가 (메타 행)
    for label in ["작가", "글쓴이", "저자", "원작"]:
        m = re.search(
            rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        if m:
            author = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            author = re.sub(r"\s+", " ", author)
            # 첫 번째 항목 (링크 또는 텍스트)
            link_m = re.search(r'<a[^>]*>([^<]+)</a>', m.group(1))
            if link_m:
                author = link_m.group(1).strip()
            if author and 1 < len(author) < 50 and "<" not in author:
                meta["author"] = author
                break

    # 장르
    for label in ["장르", "분류"]:
        m = re.search(
            rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        if m:
            inner = m.group(1)
            # 링크 텍스트 또는 직접 텍스트
            items = re.findall(r'>([^<]+)<', inner)
            items = [i.strip() for i in items if i.strip() and len(i.strip()) < 30]
            if items:
                meta["genre"] = items[:3]
                break

    # 연재 상태
    for label in ["연재상태", "상태", "연재 여부"]:
        m = re.search(
            rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        if m:
            status = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if "완결" in status:
                meta["status"] = "완결"
            elif "연재" in status:
                meta["status"] = "연재중"
            elif "단편" in status:
                meta["status"] = "단편"
            elif "휴재" in status:
                meta["status"] = "휴재"
            break

    # 표지
    cover_m = re.search(
        r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
        html
    )
    if not cover_m:
        cover_m = re.search(
            r'<img[^>]*class="[^"]*book[^"]*"[^>]*src="([^"]+)"',
            html
        )
    if cover_m:
        url = cover_m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.munpia.com" + url
        meta["cover_url"] = url

    # 설명
    desc_m = re.search(
        r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"',
        html
    )
    if desc_m:
        meta["description"] = desc_m.group(1).strip()

    return meta


# ============================================================
# 조아라 (joara.com)
# ============================================================

def search_joara(title: str) -> List[str]:
    """조아라 검색."""
    candidates = [
        f"https://www.joara.com/search/search_list.asp?keyword={quote(title)}",
        f"https://www.joara.com/search/search_list.asp?search_keyword={quote(title)}",
        f"https://www.joara.com/book/search_list.asp?keyword={quote(title)}",
    ]
    links = []
    for url in candidates:
        html = _cached_get(url)
        if not html:
            continue
        patterns = [
            r'href="([^"]*book/list/book_detail[^"]*)"',
            r'href="(/book/\d+[^"]*)"',
            r'href="(/book/view[^"]*)"',
        ]
        for p in patterns:
            for m in re.findall(p, html):
                full = "https://www.joara.com" + m if m.startswith("/") else m
                if full not in links and "search" not in full:
                    links.append(full)
        if links:
            break
    return links[:5]


def get_joara_metadata(book_url: str) -> Optional[Dict]:
    """조아라 책 상세 페이지에서 메타데이터 추출."""
    html = _cached_get(book_url)
    if not html:
        return None

    meta = {
        "source": "joara",
        "url": book_url,
    }

    # 제목
    for pat in [
        r'<title>(.*?)</title>',
        r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"',
    ]:
        m = re.search(pat, html)
        if m:
            meta["title"] = m.group(1).replace(" - 조아라", "").replace(" | 조아라", "").strip()
            break

    # 작가
    for label in ["작가", "글쓴이", "저자", "원작"]:
        m = re.search(
            rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        if m:
            inner = m.group(1)
            link_m = re.search(r'<a[^>]*>([^<]+)</a>', inner)
            if link_m:
                author = link_m.group(1).strip()
            else:
                author = re.sub(r"<[^>]+>", "", inner).strip()
                author = re.sub(r"\s+", " ", author)
            if author and 1 < len(author) < 50:
                meta["author"] = author
                break

    # 장르
    m = re.search(
        r'<th[^>]*>\s*장르\s*</th>\s*<td[^>]*>(.*?)</td>',
        html, re.DOTALL
    )
    if m:
        inner = m.group(1)
        items = re.findall(r'>([^<]+)<', inner)
        items = [i.strip() for i in items if i.strip() and len(i.strip()) < 30]
        if items:
            meta["genre"] = items[:3]

    # 연재 상태
    for label in ["연재상태", "상태", "연재"]:
        m = re.search(
            rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        if m:
            status = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if "완결" in status:
                meta["status"] = "완결"
            elif "연재" in status:
                meta["status"] = "연재중"
            elif "단편" in status:
                meta["status"] = "단편"
            elif "휴재" in status:
                meta["status"] = "휴재"
            break

    # 표지
    cover_m = re.search(
        r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
        html
    )
    if cover_m:
        url = cover_m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        meta["cover_url"] = url

    # 설명
    desc_m = re.search(
        r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"',
        html
    )
    if desc_m:
        meta["description"] = desc_m.group(1).strip()

    return meta


# ============================================================
# 듀얼 SSOT 통합
# ============================================================

def get_metadata(title: str) -> Optional[Dict]:
    """문피아/조아라 듀얼 SSOT로 메타데이터 수집.

    Returns:
        통합된 메타 dict 또는 None (둘 다 실패)
    """
    log.info(f"듀얼 SSOT 조회: {title}")

    munpia = None
    joara = None

    # 문피아 시도
    munpia_urls = search_munpia(title)
    if munpia_urls:
        munpia = get_munpia_metadata(munpia_urls[0])
        log.info(f"  문피아: {'✓' if munpia else '✗'} {munpia_urls[0][:60]}")
    else:
        log.info(f"  문피아: 검색 결과 없음")

    # 조아라 시도
    joara_urls = search_joara(title)
    if joara_urls:
        joara = get_joara_metadata(joara_urls[0])
        log.info(f"  조아라: {'✓' if joara else '✗'} {joara_urls[0][:60]}")
    else:
        log.info(f"  조아라: 검색 결과 없음")

    if not munpia and not joara:
        return None

    # 교차 검증 + 통합
    merged = {
        "title": title,
        "sources": [],
    }

    # 작가
    authors = []
    if munpia and munpia.get("author"):
        authors.append(("munpia", munpia["author"]))
    if joara and joara.get("author"):
        authors.append(("joara", joara["author"]))

    if authors:
        unique_authors = {a for _, a in authors}
        if len(unique_authors) == 1:
            merged["author"] = authors[0][1]
        else:
            # 불일치 → 가장 긴 이름 (공식명 가능성)
            merged["author"] = max((a for _, a in authors), key=len)
            merged["author_conflict"] = list(unique_authors)
        merged["sources"].extend([s for s, _ in authors])
    else:
        merged["author"] = "미상"

    # 장르
    genres = []
    if munpia and munpia.get("genre"):
        genres.extend(munpia["genre"])
    if joara and joara.get("genre"):
        genres.extend(joara["genre"])
    merged["genre"] = list(dict.fromkeys(genres))[:5]

    # 상태
    statuses = []
    if munpia and munpia.get("status"):
        statuses.append(("munpia", munpia["status"]))
    if joara and joara.get("status"):
        statuses.append(("joara", joara["status"]))

    if statuses:
        unique = {s for _, s in statuses}
        if len(unique) == 1:
            merged["status"] = statuses[0][1]
        else:
            merged["status"] = "확인 필요"
            merged["status_conflict"] = list(unique)
    else:
        merged["status"] = "unknown"

    # 표지 (큰 해상도 우선)
    covers = []
    if munpia and munpia.get("cover_url"):
        covers.append(munpia["cover_url"])
    if joara and joara.get("cover_url"):
        covers.append(joara["cover_url"])
    merged["cover_url"] = covers[0] if covers else None

    # 설명 (더 긴 것)
    descs = []
    if munpia and munpia.get("description"):
        descs.append(munpia["description"])
    if joara and joara.get("description"):
        descs.append(joara["description"])
    if descs:
        merged["description"] = max(descs, key=len)
    else:
        merged["description"] = ""

    # 원본 URL
    merged["source_urls"] = {
        "munpia": munpia.get("url") if munpia else None,
        "joara": joara.get("url") if joara else None,
    }

    return merged


# ============================================================
# DB 업데이트
# ============================================================

def update_db_and_local(novel_id: str, merged: Dict) -> None:
    """DB + 로컬 meta.json 업데이트."""
    import psycopg2
    import os

    NEON = os.getenv("NEON_DATABASE_URL", "")
    if not NEON:
        print("NEON_DATABASE_URL 환경변수 미설정, DB 업데이트 생략")
        return
    DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")

    # DB
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("""
        UPDATE ebook_novels
        SET author = %s, status = %s, updated_at = NOW()
        WHERE id = %s
    """, (merged["author"], merged["status"], novel_id))
    conn.commit()
    cur.close()
    conn.close()

    # 로컬 meta.json
    meta_file = DATA_DIR / novel_id / "meta.json"
    if meta_file.exists():
        with open(meta_file) as f:
            local = json.load(f)
        local["author"] = merged["author"]
        if merged["status"] not in ["unknown", ""]:
            local["status"] = merged["status"]
        if merged.get("description") and len(merged["description"]) > len(local.get("description", "")):
            local["description"] = merged["description"]
        with open(meta_file, "w") as f:
            json.dump(local, f, ensure_ascii=False, indent=2)


def main():
    """DB의 모든 소설에 대해 듀얼 SSOT 메타데이터 수집 + 업데이트."""
    import psycopg2
    import os

    NEON = os.getenv("NEON_DATABASE_URL", "")
    if not NEON:
        print("NEON_DATABASE_URL 환경변수 미설정")
        return
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM ebook_novels ORDER BY id")
    novels = cur.fetchall()
    cur.close()
    conn.close()

    print(f"=== 문피아/조아라 듀얼 SSOT 메타데이터 수집 ({len(novels)}개 소설) ===\n")

    for novel_id, title in novels:
        print(f"\n[{novel_id}] {title}")
        merged = get_metadata(title)
        if not merged:
            print(f"  ❌ 듀얼 SSOT 실패 - DB 값 유지")
            continue

        print(f"  → 최종:")
        for k, v in merged.items():
            if k == "source_urls":
                continue
            print(f"    {k}: {v}")
        if merged.get("source_urls"):
            print(f"    출처:")
            for s, u in merged["source_urls"].items():
                if u:
                    print(f"      {s}: {u[:70]}")

        # DB 업데이트
        update_db_and_local(novel_id, merged)
        print(f"  ✓ DB + meta.json 업데이트")

    print("\n=== 최종 상태 ===")
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, author, status FROM ebook_novels ORDER BY id")
    for row in cur.fetchall():
        print(f"  {row[0]}: author={row[1]!r}, status={row[2]!r}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()