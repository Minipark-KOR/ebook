#!/usr/bin/env python3
# Status: experimental
# Path: none — ISBNLib + Brave/DuckDuckGo 기반 소설 메타데이터 조회
"""소설 제목으로 공식 메타데이터 검색 (isbnlib + Brave Search + DuckDuckGo 사용)"""

from dataclasses import dataclass
from typing import Optional
import re
import json

from isbnlib import canonical, meta, goom, NotValidISBNError, ISBNLibException
from isbnlib.dev._exceptions import ISBNLibHTTPError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import time


@dataclass
class NovelMetadata:
    """표준화된 소설 메타데이터"""
    title: str
    authors: list[str]
    publisher: Optional[str] = None
    year: Optional[str] = None
    isbn13: Optional[str] = None
    isbn10: Optional[str] = None
    language: Optional[str] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None
    subjects: list[str] = None
    page_count: Optional[int] = None
    source: str = "goob"

    def __post_init__(self):
        if self.subjects is None:
            self.subjects = []

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "publisher": self.publisher,
            "year": self.year,
            "isbn13": self.isbn13,
            "isbn10": self.isbn10,
            "language": self.language,
            "coverUrl": self.cover_url,
            "description": self.description,
            "subjects": self.subjects,
            "pageCount": self.page_count,
            "source": self.source,
        }


class MetadataLookupError(Exception):
    """메타데이터 조회 실패"""
    pass


def _clean_author(name: str) -> str:
    """작가명에서 조사 제거"""
    return re.sub(r'(?:이|가|은|는|을|를|께서|께서)$', '', name)


def _extract_authors(text: str) -> list[str]:
    """텍스트에서 작가명 추출"""
    patterns = [
        r'소설가\s+([가-힣]{2,4})(?:이|가)?\s*(?:쓴|집필|지음)',
        r'작가\s+([가-힣]{2,4})(?:이|가)?\s*(?:집필|지음|쓴)',
        r'[》\"]\s*(?:은|는)\s+([가-힣]{2,4})(?:이|가)?\s*(?:쓴|지은)',
        r'([가-힣]{2,4})(?:이|가)?\s*(?:지음|저|작)(?:\s|$)',
        r'지은이\s*[:：]\s*([가-힣]{2,4})',
        r'저자\s+([가-힣]{2,4})',
        r'작가\s*[:：]\s*([가-힣]{2,4})',  # 작가: 꾸찌꾸찌
        r'([가-힣]{2,4})\s*\(본명',
    ]

    authors = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        authors.extend(matches)

    authors = [_clean_author(a) for a in authors]
    authors = list(dict.fromkeys(authors))

    # 거짓 양성 필터링
    false_positives = {
        '본인으로', '갤러리에', '품과', '작가', '소설가', '집필',
        '대한민국', '현대', '판타지', '장르', '웹소설', '이야기',
        '글', '사람', '추정', '본인', '자신', '대한', '민국',
        '영어', '솔로', '레벨업', '추공이', '싱숑이', '글쓴이',
        '저자', '지은이', '작성', '기자', '편집', '번역',
        '완결', '연재', '소설', '웹', '툰', '만화', '출판'
    }
    authors = [a for a in authors if a not in false_positives and len(a) >= 2]
    return authors


def _extract_publisher(text: str) -> Optional[str]:
    """텍스트에서 출판사 추출"""
    patterns = [
        r'출판사\s+([가-힣A-Za-z\s&]+?)(?:에서|으로|\(|\)|,|\.|$)',
        r'발행\s+([가-힣A-Za-z\s&]+?)(?:에서|으로|\(|\)|,|\.|$)',
        r'펴낸곳\s*[:：]\s*([가-힣A-Za-z\s&]+?)(?:,|\.|$)',
        r'(디앤씨미디어|문학동네|황금가지|알에이치코리아|비채|엘릭시르|북폴리오|시공사|민음사|창비|위즈덤하우스|다산북스|arte|한즈미디어|길벗|영진닷컴|제이펍|한빛미디어|에이콘|알라딘|교보문고|예스24|인터파크)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip() if match.lastindex else match.group(0).strip()
    return None


def _extract_year(text: str) -> Optional[str]:
    """텍스트에서 연도 추출"""
    patterns = [
        r'(\d{4})년\s*(?:출간|발행|출판|발매)',
        r'(\d{4})년\s*\d{1,2}월',
        r'(20\d{2}|19\d{2})년',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _extract_isbn(text: str) -> tuple[Optional[str], Optional[str]]:
    """텍스트에서 ISBN 추출"""
    isbn13 = None
    isbn10 = None

    # ISBN-13
    for match in re.finditer(r'(?:ISBN[-\s:]?\s*)?(97[89][-\s]?\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,6}[-\s]?\d)', text):
        isbn_clean = re.sub(r'[-\s]', '', match.group(1))
        if len(isbn_clean) == 13 and isbn13 is None:
            isbn13 = isbn_clean
            break

    # ISBN-10
    for match in re.finditer(r'(?:ISBN[-\s:]?\s*)?(\d{9}[\dX])', text):
        isbn_clean = re.sub(r'[-\s]', '', match.group(1))
        if len(isbn_clean) == 10 and isbn10 is None:
            isbn10 = isbn_clean
            break

    return isbn13, isbn10


def search_novel_by_title(
    title: str,
    service: str = "goob",
    max_results: int = 5
) -> list[NovelMetadata]:
    """
    소설 제목으로 메타데이터 검색 (다중 결과 반환)

    Args:
        title: 소설 제목 (한글/영문)
        service: 'goob'(Google Books), 'openl'(OpenLibrary), 'wiki'(Wikipedia), 'brave'(Brave/DuckDuckGo)
        max_results: 최대 결과 수

    Returns:
        NovelMetadata 리스트 (관련도 순)

    Raises:
        MetadataLookupError: 검색 실패 시
    """
    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(ISBNLibHTTPError),
        reraise=True,
    )
    def _goom_with_retry(query: str) -> list:
        return goom(query)

    # Brave/DuckDuckGo Search 사용 시
    if service == "brave":
        return _search_brave(title, max_results)

    try:
        time.sleep(0.5)  # Google Books API rate limit 완화
        results = _goom_with_retry(title)
        if not results:
            return []

        metadata_list = []
        for result in results[:max_results]:
            isbn13_raw = result.get("ISBN-13") or result.get("ISBN13")
            if not isbn13_raw:
                metadata_list.append(NovelMetadata(
                    title=result.get("Title", title),
                    authors=result.get("Authors", []),
                    source=service,
                ))
                continue

            try:
                isbn13 = canonical(isbn13_raw)
            except (NotValidISBNError, ValueError):
                continue

            try:
                data = meta(isbn13, service=service)
            except ISBNLibException:
                continue

            if not data:
                continue

            cover = data.get("Cover", {})
            cover_url = (
                cover.get("large") or
                cover.get("medium") or
                cover.get("small") or
                cover.get("thumbnail")
            )

            metadata_list.append(NovelMetadata(
                title=data.get("Title", title),
                authors=data.get("Authors", []),
                publisher=data.get("Publisher"),
                year=str(data.get("Year")) if data.get("Year") else None,
                isbn13=isbn13,
                isbn10=data.get("ISBN-10"),
                language=data.get("Language"),
                cover_url=cover_url,
                description=data.get("Description"),
                subjects=data.get("Subjects", []),
                page_count=data.get("Pages"),
                source=service,
            ))

        return metadata_list

    except ISBNLibHTTPError:
        return _search_openlibrary(title, max_results)
    except ISBNLibException as e:
        raise MetadataLookupError(f"ISBNLib 검색 실패: {e}") from e
    except Exception as e:
        raise MetadataLookupError(f"예상치 못한 오류: {e}") from e


def _search_brave(title: str, max_results: int = 5) -> list[NovelMetadata]:
    """Brave Search API 또는 DuckDuckGo HTML 스크래핑을 통한 메타데이터 검색"""
    import requests
    import os

    # 1. Brave API 키가 있으면 직접 호출
    brave_api_key = os.getenv("BRAVE_API_KEY")
    if brave_api_key:
        results = _search_brave_direct(title, max_results, brave_api_key)
        if results:
            return results

    # 2. DuckDuckGo HTML 스크래핑 폴백
    return _search_duckduckgo(title, max_results)


def _search_brave_direct(title: str, max_results: int, api_key: str) -> list[NovelMetadata]:
    """Brave Search API 직접 호출"""
    import requests
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params = {"q": f"{title} 소설 메타데이터 ISBN 출판사 작가", "count": max_results, "country": "KR", "search_lang": "ko"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            metadata = _extract_metadata_from_brave_result(title, item)
            if metadata:
                results.append(metadata)

        return results
    except Exception:
        return []


def _search_duckduckgo(title: str, max_results: int = 5) -> list[NovelMetadata]:
    """DuckDuckGo HTML 스크래핑 폴백 (API 키 불필요)"""
    import requests
    try:
        url = "https://html.duckduckgo.com/html/"
        params = {"q": f"{title} 소설 ISBN 출판사 작가 연도"}
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ebook-metadata/1.0)"}
        resp = requests.post(url, data=params, headers=headers, timeout=10)
        resp.raise_for_status()

        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)

        results = []
        for snippet_html in snippets[:max_results]:
            snippet = re.sub(r'<[^>]+>', '', snippet_html)
            snippet = re.sub(r'&[a-z]+;', ' ', snippet)
            metadata = _extract_metadata_from_snippet(title, snippet)
            if metadata:
                results.append(metadata)

        return results
    except Exception:
        return []


def _extract_metadata_from_brave_result(title: str, item: dict) -> Optional[NovelMetadata]:
    snippet = item.get("description", "") or item.get("snippet", "")
    return _extract_metadata_from_snippet(title, snippet)


def _extract_metadata_from_snippet(title: str, snippet: str) -> Optional[NovelMetadata]:
    if not snippet or len(snippet) < 20:
        return None

    combined = f"{title} {snippet}"

    authors = _extract_authors(combined)
    publisher = _extract_publisher(combined)
    year = _extract_year(combined)
    isbn13, isbn10 = _extract_isbn(combined)

    return NovelMetadata(
        title=title,
        authors=authors,
        publisher=publisher,
        year=year,
        isbn13=isbn13,
        isbn10=isbn10,
        language="ko",
        cover_url=None,
        description=snippet[:500] if snippet else None,
        subjects=[],
        page_count=None,
        source="brave",
    )


def _search_openlibrary(title: str, max_results: int = 5) -> list[NovelMetadata]:
    """Open Library Search API 폴백 (API 키 불필요)"""
    import requests
    try:
        url = "https://openlibrary.org/search.json"
        params = {"title": title, "limit": max_results}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for doc in data.get("docs", [])[:max_results]:
            isbn_list = doc.get("isbn", [])
            isbn13 = next((i for i in isbn_list if len(i) == 13), None)
            isbn10 = next((i for i in isbn_list if len(i) == 10), None)

            cover_id = doc.get("cover_i")
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None

            results.append(NovelMetadata(
                title=doc.get("title", title),
                authors=doc.get("author_name", []),
                publisher=doc.get("publisher", [None])[0] if doc.get("publisher") else None,
                year=str(doc.get("first_publish_year")) if doc.get("first_publish_year") else None,
                isbn13=isbn13,
                isbn10=isbn10,
                language=doc.get("language", [None])[0] if doc.get("language") else None,
                cover_url=cover_url,
                description=None,
                subjects=doc.get("subject", []),
                page_count=doc.get("number_of_pages_median"),
                source="openl",
            ))
        return results
    except Exception:
        return []


def get_best_match(title: str, service: str = "goob") -> Optional[NovelMetadata]:
    """가장 관련도 높은 단일 결과 반환"""
    results = search_novel_by_title(title, service, max_results=1)
    return results[0] if results else None


def enrich_novel_metadata(
    title: str,
    author: Optional[str] = None,
    service: str = "goob"
) -> NovelMetadata:
    """
    제목(+선택적 저자)으로 검색하여 가장 적합한 메타데이터 반환.
    """
    query = f"{title} {author}" if author else title
    results = search_novel_by_title(query, service, max_results=3)

    if not results:
        results = search_novel_by_title(title, service, max_results=1)

    if results:
        best = results[0]
        if author and author not in best.authors:
            best.authors.insert(0, author)
        return best

    return NovelMetadata(title=title, authors=[author] if author else [], source=service)


def lookup(title: str, author: str = None) -> dict:
    """간편 조회: dict 반환"""
    return enrich_novel_metadata(title, author).to_dict()


if __name__ == "__main__":
    import json
    import sys

    test_titles = [
        "나 혼자만 레벨업",
        "전지적 독자 시점",
        "하남자의 탑 공략법",
        "해리 포터와 마법사의 돌",
        "The Name of the Wind",
    ]

    for title in test_titles:
        print(f"\n{'='*60}")
        print(f"검색: {title}")
        print(f"{'='*60}")
        try:
            result = lookup(title)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except MetadataLookupError as e:
            print(f"에러: {e}", file=sys.stderr)
