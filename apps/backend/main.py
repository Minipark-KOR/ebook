#!/usr/bin/env python3
# Status: experimental
# Path: none — 초기 구현
"""FastAPI 백엔드 - 웹소설 리더 API"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 환경 변수 로드
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

from routers import novels, chapters, metadata

app = FastAPI(
    title="eBook API",
    description="웹소설 리더를 위한 API",
    version="0.1.0",
)

# CORS 설정
cors_origins_str = os.getenv("CORS_ORIGINS", '["http://localhost:3000"]')
import json

cors_origins = json.loads(cors_origins_str)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일 - 표지 이미지
COVERS_DIR = Path("/opt/ai_data/flaresolverr/covers")
COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/covers", StaticFiles(directory=str(COVERS_DIR)), name="covers")

# 라우터 등록
app.include_router(novels.router, prefix="/api", tags=["novels"])
app.include_router(chapters.router, prefix="/api", tags=["chapters"])
app.include_router(metadata.router, prefix="/api", tags=["metadata"])


@app.get("/")
async def root():
    return {"message": "eBook API"}


@app.get("/health")
async def health():
    return {"status": "ok"}
