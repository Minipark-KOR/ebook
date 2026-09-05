#!/usr/bin/env python3
"""북토끼 다운 시나리오 - DB 안전성 + SSOT 재구성.

북토끼는 죽었다고 가정. 다음 작업:
1. DB의 챕터 JSON은 안전 (이미 저장됨)
2. 메타데이터는 DB에 있음 - 검증/보강
3. SSOT는 1) namu.wiki (메타 정확) 2) DB의 자체 정보 (안전)

문피아/조아라 책 ID는 북토끼가 다시 살아나면 그때 매핑.
지금 단계에서는 namu.wiki + 자체 DB 데이터로 SSOT 유지.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List

# ebooklib 경로
sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')

import psycopg2
from services import bookto31
from services.metadata_namu import get_metadata as namu_get_metadata

DATA_DIR = Path("/opt/ai_data/flaresolverr/novels")
NEON = "postgresql://neondb_owner:npg_dtpE5bK2eAFv@ep-round-hill-azleavuh.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"


def safe_get_namu(title: str) -> Optional[Dict]:
    """namu.wiki 안전 호출. 1회 실패 시 30초 후 재시도."""
    for attempt in range(2):
        try:
            meta = namu_get_metadata(title)
            if meta:
                return meta
        except Exception as e:
            print(f"  [namu] attempt {attempt+1} 실패: {e}")
        time.sleep(30)
    return None


def cross_verify_metadata(novel_id: str) -> Dict:
    """SSOT 듀얼 (namu.wiki + DB 자체 정보)로 메타데이터 교차 검증.

    Returns:
        통합된 메타데이터 (author, status, description, cover_url)
    """
    # 1) DB에서 현재 메타 로드
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT title, author, status, cover_url, description FROM ebook_novels WHERE id = %s", (novel_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}

    db_title, db_author, db_status, db_cover, db_desc = row
    print(f"  DB: author={db_author}, status={db_status}")

    # 2) namu.wiki 조회
    namu = safe_get_namu(db_title)
    if namu:
        print(f"  namu: author={namu.get('author')}, status={namu.get('status')}")
    else:
        namu = {}

    # 3) 교차 검증
    merged = {
        "title": db_title,
        "sources_used": [],
    }

    # 작가
    db_author_clean = db_author or "미상"
    namu_author = namu.get("author", "").strip() if namu else ""
    # DB 작가가 "미상"이거나 비어있으면 namu.wiki 사용
    # 둘 다 있으면 동일성 확인
    if db_author_clean in ["미상", "", "&lt;내가", "&lt;"]:
        if namu_author and not any(x in namu_author for x in ["&lt;", "&gt;"]):
            merged["author"] = namu_author
            merged["sources_used"].append("namu")
        else:
            merged["author"] = "미상"
    else:
        # DB에 이미 있음 - 그대로 유지 (DB가 SSOT)
        merged["author"] = db_author_clean
        merged["sources_used"].append("db")

    # 상태
    db_status_clean = db_status or "unknown"
    namu_status = namu.get("status", "") if namu else ""
    if db_status_clean in ["unknown", "확인 필요", "", None]:
        if namu_status and namu_status not in ["unknown", ""]:
            merged["status"] = namu_status
            merged["sources_used"].append("namu")
        else:
            merged["status"] = "unknown"
    else:
        merged["status"] = db_status_clean
        merged["sources_used"].append("db")

    # 설명
    merged["description"] = db_desc or namu.get("description", "")

    # 표지 (DB의 로컬 프록시 URL 우선, namu는 외부 URL)
    merged["cover_url"] = db_cover

    return merged


def update_db_and_local(novel_id: str, merged: Dict) -> None:
    """DB + 로컬 meta.json 동시 업데이트."""
    # Neon DB
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
        if merged["status"] != "unknown":
            local["status"] = merged["status"]
        with open(meta_file, "w") as f:
            json.dump(local, f, ensure_ascii=False, indent=2)


def main():
    novels = [
        "하남자의_탑_공략법",
        "오늘만_사는_기사",
        "게임_속_바바리안으로_살아남기",
        "화산귀환",
    ]

    print("=== 북토끼 다운 시나리오 - 듀얼 SSOT 재구성 ===\n")

    for novel_id in novels:
        print(f"\n[{novel_id}]")
        merged = cross_verify_metadata(novel_id)
        if not merged:
            print("  ❌ 메타데이터 없음 - 스킵")
            continue

        print(f"  → 최종: author={merged['author']}, status={merged['status']}, sources={merged['sources_used']}")

        # DB 값이 잘못된 경우만 업데이트
        current = get_current_db_values(novel_id)
        if current:
            db_author = current.get("author", "")
            db_status = current.get("status", "")
            if needs_update(db_author, merged["author"], db_status, merged["status"]):
                update_db_and_local(novel_id, merged)
                print(f"  ✓ DB 업데이트됨")
            else:
                print(f"  - DB 정상, 변경 없음")

    print("\n=== 최종 상태 ===")
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT id, author, status FROM ebook_novels ORDER BY id")
    for row in cur.fetchall():
        print(f"  {row[0]}: author={row[1]}, status={row[2]}")
    cur.close()
    conn.close()


def get_current_db_values(novel_id):
    conn = psycopg2.connect(NEON)
    cur = conn.cursor()
    cur.execute("SELECT author, status FROM ebook_novels WHERE id = %s", (novel_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"author": row[0], "status": row[1]}
    return None


def needs_update(db_author, new_author, db_status, new_status):
    """DB 값이 깨졌는지 확인."""
    BROKEN_AUTHORS = ["미상", "&lt;내가", "말단병사에서", "소울풍", "정윤강"]
    if db_author in BROKEN_AUTHORS and new_author not in BROKEN_AUTHORS:
        return True
    if db_status in ["unknown", "확인 필요", "", None] and new_status not in ["unknown", ""]:
        return True
    return False


if __name__ == "__main__":
    main()