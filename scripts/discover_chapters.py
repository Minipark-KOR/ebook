#!/usr/bin/env python3
"""북토끼 카테고리/작품 페이지에서 모든 회차 wr_id를 자동으로 큐에 추가.

- 북토끼의 회차 nav는 30개씩 페이지로 나뉨 (spage 파라미터)
- 작품 메인 wr_id(예: 25575)에서 시작 → 모든 spage 순회
- 각 페이지의 item-subject 링크에서 wr_id + 회차 번호 추출
- 큐에 일괄 추가 (이미 있으면 스킵)

사용법:
  python3 scripts/discover_chapters.py <작품_메인_wr_id> [novel_title]
  예: python3 scripts/discover_chapters.py 25575 "오늘만 사는 기사"
"""

import sys
import re
import time
from pathlib import Path

# 경로 설정
sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')
from services import bookto31
from services.bookto31 import extract_chapter_wr_ids_from_index


def discover_chapters(novel_main_wr_id: int, novel_title: str, max_pages: int = 50):
    """북토끼 작품의 모든 회차 wr_id 발견.

    Args:
        novel_main_wr_id: 작품 메인 페이지 wr_id (북토끼에서 직접 확인 필요)
        novel_title: 큐에 등록할 때 사용할 제목
        max_pages: 최대 페이지 수 (기본 50 = 1500화)

    Returns:
        발견된 (wr_id, chapter) 튜플 목록 (회차 오름차순)
    """
    all_chapters = []
    seen_wr_ids = set()

    for spage in range(1, max_pages + 1):
        url = f"https://bookto31.com/bbs/board.php?bo_table=novel&wr_id={novel_main_wr_id}&spage={spage}"
        try:
            html = bookto31._fetch_with_flaresolverr(url, rate_limit=False)
        except Exception as e:
            print(f"[오류] spage={spage}: {e}")
            break

        if not html or len(html) < 1000:
            print(f"[중단] spage={spage} 응답 없음 (작품 끝)")
            break

        page_chapters = extract_chapter_wr_ids_from_index(html)
        if not page_chapters:
            print(f"[중단] spage={spage} 회차 없음")
            break

        new_count = 0
        for wr_id, chapter in page_chapters:
            if wr_id not in seen_wr_ids and wr_id != novel_main_wr_id:
                seen_wr_ids.add(wr_id)
                all_chapters.append((wr_id, chapter))
                new_count += 1

        print(f"  spage={spage}: {new_count}개 신규 (누적 {len(all_chapters)})")

        if new_count == 0:
            # 중복만 있으면 다음 spage로 (보통 spage 끝)
            # 단, spage가 1이면서 중복이면 종료
            if spage > 1:
                break

        # 챕터 번호가 연속되지 않으면 (예: 1 → 3) 종료 가능성
        # 하지만 spage 경계에서 29, 30, 1, 2 형태로 나옴 → 무조건 계속

    return all_chapters


def add_to_queue(chapters, novel_title):
    """ebook_queue.py로 일괄 추가."""
    import subprocess
    queue_script = "/opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py"
    added = 0
    for wr_id, chapter in chapters:
        # 우선순위: 최근 회차가 높음 (마지막 챕터 먼저)
        priority = 1 if chapter >= 800 else 5
        result = subprocess.run(
            ["python3", queue_script, "add", str(wr_id), novel_title, str(priority)],
            capture_output=True, text=True
        )
        if "추가됨" in result.stdout:
            added += 1
    return added


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    novel_main_wr_id = int(sys.argv[1])
    novel_title = sys.argv[2] if len(sys.argv) > 2 else f"소설 {novel_main_wr_id}"

    # auto 플래그가 먼저 오고, max_pages는 그 다음
    auto_mode = len(sys.argv) > 3 and sys.argv[3] == "auto"
    if auto_mode:
        max_pages = int(sys.argv[4]) if len(sys.argv) > 4 else 50
    else:
        max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    print(f"=== 북토끼 챕터 자동 발견 ===")
    print(f"작품 메인 wr_id: {novel_main_wr_id}")
    print(f"제목: {novel_title}")
    print(f"최대 페이지: {max_pages}")
    print()

    chapters = discover_chapters(novel_main_wr_id, novel_title, max_pages)
    print(f"\n총 {len(chapters)}개 회차 발견")

    if not chapters:
        print("발견된 회차 없음")
        sys.exit(1)

    # 즉시 큐에 추가할지 확인
    if auto_mode:
        added = add_to_queue(chapters, novel_title)
        print(f"큐에 {added}개 추가됨")
    else:
        print(f"\n큐에 추가하려면:")
        print(f"  python3 scripts/discover_chapters.py {novel_main_wr_id} '{novel_title}' auto")
        print()
        print(f"또는 직접 ebook_queue.py로 추가")


if __name__ == "__main__":
    main()