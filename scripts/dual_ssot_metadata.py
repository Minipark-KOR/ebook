#!/usr/bin/env python3
"""문피아/조아라 듀얼 SSOT로 메타데이터 구성.

북토끼가 죽었다고 가정. 문피아/조아라 두 출처에서 메타데이터를 가져와
교차 검증 후 DB에 저장. 한쪽이 죽어도 다른 쪽이 SSOT 역할.

각 사이트에서:
- 책 검색 (제목)
- 책 상세 페이지 (메타데이터)
- 챕터 목록

수집 메타데이터:
- 제목, 작가, 장르, 상태(연재중/완결), 설명, 표지 이미지
"""

import re
import sys
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dual_ssot")

# 캐시 (크롤링 부담 줄이기)
CACHE_DIR = Path("/opt/ai_data/flaresolverr/dual_ssot_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class DualMetadataSSOT:
    """문피아/조아라 듀얼 SSOT 크롤러."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        self.rate_limit_sec = 5  # 요청 간격

    def _get(self, url: str, timeout: int = 15) -> Optional[str]:
        """캐시 + rate limit + GET."""
        cache_key = CACHE_DIR / (url.replace("/", "_").replace(":", "_")[:200] + ".html")
        if cache_key.exists():
            age = time.time() - cache_key.stat().st_mtime
            if age < 86400:  # 24시간 캐시
                return cache_key.read_text(encoding="utf-8", errors="ignore")

        time.sleep(self.rate_limit_sec)
        try:
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            html = resp.text
            cache_key.write_text(html, encoding="utf-8", errors="ignore")
            return html
        except Exception as e:
            log.warning(f"  fetch 실패: {url} ({e})")
            return None

    # ---- 문피아 ----

    def search_munpia(self, title: str) -> list:
        """문피아 검색. 책 상세 URL 목록."""
        # API 시도
        api_url = f"https://www.munpia.com/search/search_list.do?search_type=1&search_keyword={quote(title)}"
        html = self._get(api_url)
        if not html:
            return []

        # 책 링크 추출 (다양한 패턴)
        links = re.findall(
            r'href="([^"]*(?:book|novel|detail|view)[^"]*\d+[^"]*)"',
            html
        )
        # 중복 제거 + 정규화
        seen = set()
        unique = []
        for link in links:
            normalized = link.split("?")[0]
            if normalized not in seen:
                seen.add(normalized)
                unique.append("https://www.munpia.com" + normalized if link.startswith("/") else link)
        return unique[:5]

    def get_munpia_metadata(self, book_url: str) -> Optional[Dict]:
        """문피아 책 상세 페이지에서 메타데이터 추출."""
        html = self._get(book_url)
        if not html:
            return None

        meta = {"source": "munpia", "url": book_url}

        # 제목
        title_m = re.search(r'<title>(.*?)</title>', html)
        if title_m:
            meta["title"] = title_m.group(1).replace(" - 문피아", "").replace(" | 문피아", "").strip()

        # 작가 (다양한 패턴)
        for label in ["작가", "글쓴이", "저자", "원작"]:
            m = re.search(rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
            if m:
                author = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                author = re.sub(r'\s+', ' ', author)
                if author and len(author) < 50:
                    meta["author"] = author
                    break

        # 장르
        for label in ["장르", "분류"]:
            m = re.search(rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
            if m:
                genre = re.sub(r'<[^>]+>', '|', m.group(1))
                genre = re.sub(r'\s+', '', genre)
                if genre:
                    meta["genre"] = [g for g in genre.split("|") if g][:3]
                    break

        # 연재 상태
        for label in ["연재상태", "상태", "연재"]:
            m = re.search(rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
            if m:
                status = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if "완결" in status:
                    meta["status"] = "완결"
                elif "연재" in status:
                    meta["status"] = "연재중"
                break

        # 표지
        cover_m = re.search(r'<img[^>]*src="([^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"[^>]*(?:cover|book|thumb)', html, re.DOTALL)
        if not cover_m:
            cover_m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
        if cover_m:
            url = cover_m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://www.munpia.com" + url
            meta["cover_url"] = url

        # 설명 (og:description 우선)
        desc_m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"', html)
        if desc_m:
            meta["description"] = desc_m.group(1).strip()

        return meta

    # ---- 조아라 ----

    def search_joara(self, title: str) -> list:
        """조아라 검색."""
        url = f"https://www.joara.com/search/search_list.asp?keyword={quote(title)}"
        html = self._get(url)
        if not html:
            return []

        # 책 링크 (book/list/...)
        links = re.findall(r'href="([^"]*book/list/book_detail[^"]*)"', html)
        if not links:
            links = re.findall(r'href="(/book/[^"]+)"', html)
        seen = set()
        unique = []
        for link in links:
            full = "https://www.joara.com" + link if link.startswith("/") else link
            if full not in seen:
                seen.add(full)
                unique.append(full)
        return unique[:5]

    def get_joara_metadata(self, book_url: str) -> Optional[Dict]:
        """조아라 책 상세 페이지에서 메타데이터 추출."""
        html = self._get(book_url)
        if not html:
            return None

        meta = {"source": "joara", "url": book_url}

        # 제목
        title_m = re.search(r'<title>(.*?)</title>', html)
        if title_m:
            meta["title"] = title_m.group(1).replace(" - 조아라", "").replace(" | 조아라", "").strip()

        # 작가
        for label in ["작가", "글쓴이", "저자"]:
            m = re.search(rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
            if m:
                author = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                author = re.sub(r'\s+', ' ', author)
                if author and len(author) < 50:
                    meta["author"] = author
                    break

        # 장르
        m = re.search(r'<th[^>]*>\s*장르\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
        if m:
            genre = re.sub(r'<[^>]+>', '|', m.group(1))
            genre = re.sub(r'\s+', '', genre)
            if genre:
                meta["genre"] = [g for g in genre.split("|") if g][:3]

        # 상태
        m = re.search(r'<th[^>]*>\s*연재상태\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
        if not m:
            m = re.search(r'<th[^>]*>\s*상태\s*</th>\s*<td[^>]*>(.*?)</td>', html, re.DOTALL)
        if m:
            status = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if "완결" in status:
                meta["status"] = "완결"
            elif "연재" in status:
                meta["status"] = "연재중"

        # 표지
        cover_m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
        if cover_m:
            url = cover_m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            meta["cover_url"] = url

        # 설명
        desc_m = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"', html)
        if desc_m:
            meta["description"] = desc_m.group(1).strip()

        return meta

    # ---- 듀얼 SSOT ----

    def get_metadata(self, title: str) -> Optional[Dict]:
        """문피아/조아라 듀얼로 메타데이터 수집. 교차 검증.

        Returns:
            통합된 메타데이터 dict 또는 None
        """
        log.info(f"듀얼 SSOT 조회: {title}")

        munpia_meta = None
        joara_meta = None

        # 문피아 검색 + 첫 번째 책
        munpia_urls = self.search_munpia(title)
        if munpia_urls:
            munpia_meta = self.get_munpia_metadata(munpia_urls[0])
            log.info(f"  문피아: {'✓ ' + munpia_meta.get('title', '?') if munpia_meta else '✗'}")

        # 조아라 검색 + 첫 번째 책
        joara_urls = self.search_joara(title)
        if joara_urls:
            joara_meta = self.get_joara_metadata(joara_urls[0])
            log.info(f"  조아라: {'✓ ' + joara_meta.get('title', '?') if joara_meta else '✗'}")

        if not munpia_meta and not joara_meta:
            return None

        # 통합 (교차 검증)
        merged = {"title": title, "sources": []}
        if munpia_meta:
            merged["sources"].append("munpia")
        if joara_meta:
            merged["sources"].append("joara")

        # 작가: 두 출처가 모두 있으면 검증
        authors = []
        if munpia_meta and munpia_meta.get("author"):
            authors.append(munpia_meta["author"])
        if joara_meta and joara_meta.get("author"):
            authors.append(joara_meta["author"])

        if authors:
            unique = set(authors)
            if len(unique) == 1:
                merged["author"] = authors[0]  # 일치 → 확정
            else:
                # 불일치 → 더 긴/공식 이름 우선
                merged["author"] = max(authors, key=len)
                merged["author_conflict"] = list(unique)
        else:
            merged["author"] = "미상"

        # 장르
        genres = []
        if munpia_meta and munpia_meta.get("genre"):
            genres.extend(munpia_meta["genre"])
        if joara_meta and joara_meta.get("genre"):
            genres.extend(joara_meta["genre"])
        merged["genre"] = list(dict.fromkeys(genres))[:3]  # 중복 제거

        # 상태
        statuses = []
        if munpia_meta and munpia_meta.get("status"):
            statuses.append(munpia_meta["status"])
        if joara_meta and joara_meta.get("status"):
            statuses.append(joara_meta["status"])
        if statuses:
            unique = set(statuses)
            if len(unique) == 1:
                merged["status"] = statuses[0]
            else:
                merged["status"] = "확인 필요"
        else:
            merged["status"] = "unknown"

        # 표지
        if munpia_meta and munpia_meta.get("cover_url"):
            merged["cover_url"] = munpia_meta["cover_url"]
        elif joara_meta and joara_meta.get("cover_url"):
            merged["cover_url"] = joara_meta["cover_url"]
        else:
            merged["cover_url"] = None

        # 설명
        descs = []
        if munpia_meta and munpia_meta.get("description"):
            descs.append(munpia_meta["description"])
        if joara_meta and joara_meta.get("description"):
            descs.append(joara_meta["description"])
        if descs:
            # 더 긴 설명 채택
            merged["description"] = max(descs, key=len)
        else:
            merged["description"] = ""

        return merged


def main():
    """4개 소설에 대해 듀얼 SSOT 메타데이터 수집 + DB 업데이트."""
    import psycopg2

    NEON = "postgresql://neondb_owner:npg_dtpE5bK2eAFv@ep-round-hill-azleavuh.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")

    ssot = DualMetadataSSOT()
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()

    # DB의 4개 소설에 대해 듀얼 SSOT 메타데이터 수집
    for novel_id in ["하남자의_탑_공략법", "오늘만_사는_기사", "게임_속_바바리안으로_살아남기", "화산귀환"]:
        # 책 제목 (DB에서 조회)
        cur.execute("SELECT title, author, status FROM ebook_novels WHERE id = %s", (novel_id,))
        row = cur.fetchone()
        if not row:
            continue
        current_title, current_author, current_status = row

        log.info(f"\n=== {novel_id} ===")
        log.info(f"  현재 DB: title={current_title}, author={current_author}, status={current_status}")

        # 듀얼 SSOT로 메타데이터 수집
        meta = ssot.get_metadata(current_title)
        if not meta:
            log.warning(f"  듀얼 SSOT 실패 - DB 값 유지")
            continue

        log.info(f"  듀얼 SSOT: author={meta.get('author')}, status={meta.get('status')}, sources={meta['sources']}")

        # DB 업데이트 (교차 검증된 값으로)
        new_author = meta.get("author", "미상")
        if not current_author or current_author in ["미상", "&lt;내가", "말단병사에서", "소울풍", "정윤강"]:
            # 잘못됐거나 없으면 SSOT 값으로 교체
            cur.execute("""
                UPDATE ebook_novels
                SET author = %s, status = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_author, meta.get("status", current_status), novel_id))
            log.info(f"  ✓ DB 업데이트: author={new_author}, status={meta.get('status')}")

        # 로컬 meta.json도 업데이트
        meta_file = DATA_DIR / novel_id / "meta.json"
        if meta_file.exists():
            with open(meta_file) as f:
                local_meta = json.load(f)
            if not local_meta.get("author") or local_meta.get("author") in ["미상", "&lt;내가"]:
                local_meta["author"] = new_author
                if meta.get("status") and meta["status"] != "unknown":
                    local_meta["status"] = meta["status"]
                with open(meta_file, "w") as f:
                    json.dump(local_meta, f, ensure_ascii=False, indent=2)

    conn.commit()
    cur.close()
    conn.close()

    log.info("\n=== 최종 DB 상태 ===")
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, author, status FROM ebook_novels ORDER BY id")
    for row in cur.fetchall():
        log.info(f"  {row[0]}: author={row[1]}, status={row[2]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()