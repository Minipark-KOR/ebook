#!/usr/bin/env python3
# Status: new
# Path: ebooklib/apps/backend/lib/database.py
"""SQLite 데이터베이스 관리 — 메타데이터 + 인덱스 계층.

JSON 파일은 소스 오브 데이터로 유지하고, SQLite는 빠른 조회/검색용 인덱스 역할.
WAL 모드로 동시 읽기 성능 향상.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path("/opt/ai_data/flaresolverr/ebooklib.db")


def get_connection() -> sqlite3.Connection:
    """SQLite 연결 (WAL 모드, 외래키 활성화)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """DB 초기화 — 테이블 생성 (없으면 생성)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
-- 작품 메타데이터
CREATE TABLE IF NOT EXISTS novels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT DEFAULT '미상',
    media_type TEXT DEFAULT 'novel',
    status TEXT DEFAULT '연재중',
    cover_url TEXT,
    description TEXT DEFAULT '',
    publisher TEXT DEFAULT '',
    genre TEXT DEFAULT '[]',
    total_chapters INTEGER DEFAULT 0,
    namu_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 챕터 인덱스 (빠른 조회용)
CREATE TABLE IF NOT EXISTS chapters (
    wr_id INTEGER PRIMARY KEY,
    novel_id TEXT NOT NULL,
    chapter INTEGER,
    title TEXT,
    content_file TEXT NOT NULL,
    content_length INTEGER DEFAULT 0,
    collected_at TEXT,
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chapters_novel ON chapters(novel_id, chapter);
CREATE INDEX IF NOT EXISTS idx_chapters_novel_id ON chapters(novel_id);

-- 읽기 진행률
CREATE TABLE IF NOT EXISTS reading_progress (
    novel_id TEXT PRIMARY KEY,
    wr_id INTEGER,
    chapter INTEGER,
    percentage REAL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (novel_id) REFERENCES novels(id) ON DELETE CASCADE
);
"""
