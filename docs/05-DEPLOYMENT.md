# 배포 가이드

> ebooklib 시스템을 Vercel과 컨테이너 인프라에 배포하는 단계별 가이드.

## 환경 구성

### 호스팅
- **프론트엔드 + 백엔드 API**: Vercel (Monorepo 단일 배포)
- **FlareSolverr**: 로컬 서버 (Podman Quadlet)
- **데이터 스토리지**: 로컬 파일시스템 (`/opt/ai_data/`)

### 시스템 요구사항
- **로컬 서버** (FlareSolverr):
 - ARM64 또는 x86_64 Linux
 - Podman 4.x + systemd
 - 2GB RAM, 2 CPU core (FlareSolverr용)
 - ARM64 이미지: `ghcr.io/flaresolverr/flaresolverr:latest`
- **Vercel**:
 - Hobby ($0) 또는 Pro 계정
 - Python Functions 지원 (Hobby: 10s timeout, Pro: 60s)

## Vercel 배포

### 1. 프로젝트 설정

**중요**: Vercel 프로젝트 생성 시 **Root Directory = `/` (루트)**.

```
Project Settings:
  - Root Directory: /
  - Framework Preset: Next.js
  - Build Command: npm run build --prefix apps/frontend && pip install -r apps/backend/requirements.txt
  - Output Directory: apps/frontend/.next
  - Functions:
    - Entry: apps/backend/main.py
    - maxDuration: 30s (Hobby) 또는 60s (Pro)
```

### 2. 환경변수 설정

Vercel Dashboard → Settings → Environment Variables:

```
CORS_ORIGINS = ["https://miniebook.vercel.app"]
ENV = production
DEBUG = false
```

선택적:
```
BRAVE_API_KEY = your_brave_api_key  # 메타데이터 Brave 검색
```

### 3. vercel.json (루트)

```json
{
  "version": 2,
  "builds": [
    {
      "src": "apps/backend/main.py",
      "use": "@vercel/python",
      "config": {
        "maxDuration": 30,
        "memory": 1024
      }
    }
  ],
  "routes": [
    { "src": "/api/(.*)", "dest": "apps/backend/main.py" },
    { "src": "/(.*)", "dest": "apps/frontend/$1" }
  ]
}
```

### 4. requirements.txt (백엔드)

```txt
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
python-dotenv>=1.0.0
isbnlib>=3.10,<3.12
tenacity>=9.0.0
requests>=2.32.0
ebooklib>=0.20
lxml>=6.0.0
```

### 5. 배포 명령

```bash
# Vercel CLI 설치
npm install -g vercel

# 최초 배포 (프로젝트 연결)
cd /opt/workspace/ebooklib
vercel --prod

# 환경변수 설정
vercel env add CORS_ORIGINS production

# 이후 배포
git push origin main  # 자동 배포 (GitHub 연동 시)
# 또는 수동
vercel --prod
```

## 로컬 FlareSolverr 배포

### 1. Podman Quadlet 설정

`~/.config/containers/systemd/svc.pod`:
```ini
[Unit]
Description=svc — Postgres + MCP shared network pod

[Pod]
PodName=svc
Network=devforge-net
PublishPort=127.0.0.1:8000:8000
PublishPort=127.0.0.1:8191:8191
ExitPolicy=continue

[Service]
Restart=always
RestartSec=15
TimeoutStopSec=120

[Install]
WantedBy=default.target
```

**중요**: 80, 443 포트는 caddy가 담당하므로 제외.

`~/.config/containers/systemd/container-flaresolverr.container`:
```ini
[Unit]
Description=FlareSolverr container (svc pod)
After=svc-pod.service
Requires=svc-pod.service

[Container]
Image=ghcr.io/flaresolverr/flaresolverr:latest
Pod=svc.pod
Environment=LOG_LEVEL=info
Environment=HEADLESS=true
Environment=DISABLE_MEDIA=true
Environment=BROWSER_WAIT_TIMEOUT=2
Volume=/opt/ai_data/flaresolverr:/app/cache:Z

[Service]
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
```

`~/.config/systemd/user/container-flaresolverr.service`:
```ini
[Unit]
Description=FlareSolverr — Cloudflare bypass proxy (svc pod)
After=svc-pod.service
Requires=svc-pod.service

[Service]
Type=simple
ExecStartPre=-/usr/bin/podman rm -f flaresolverr
ExecStart=/usr/bin/podman run --name flaresolverr --rm --pod=svc \
  -v /opt/ai_data/flaresolverr:/app/cache:Z \
  -e LOG_LEVEL=info \
  -e HEADLESS=true \
  -e DISABLE_MEDIA=true \
  -e BROWSER_WAIT_TIMEOUT=2 \
  ghcr.io/flaresolverr/flaresolverr:latest
ExecStop=/usr/bin/podman stop flaresolverr
Restart=always
RestartSec=15
TimeoutStartSec=120
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

### 2. systemd 등록

```bash
systemctl --user daemon-reload
systemctl --user enable --now svc-pod container-flaresolverr

# 검증
podman pod ls
curl http://127.0.0.1:8191/health
```

### 3. 포트 충돌 해결

**문제**: svc.pod의 80/443이 caddy와 충돌.

**해결**: svc.pod에서 80/443 PublishPort 제거 (caddy가 담당).

```bash
# caddy 확인
ss -tlnp | grep ":80\|:443"

# caddy가 80/443 점유하면 svc.pod 정의에서 해당 라인 제거
```

## 데이터 디렉토리 구조

```
/opt/ai_data/
├── flaresolverr/
│   ├── rate_limiter.db          # SQLite (URL별 마지막 요청 시각)
│   └── novels/
│       └── {소설ID}/
│           ├── meta.json
│           └── {wr_id}.json
```

**백업 권장**:
- 소설 데이터: 일 1회 (총 4-5MB/소설)
- rate_limiter.db: 주 1회 (16KB)

## 환경별 설정

### .env (백엔드 로컬)
```env
ENV=development
DEBUG=true
CORS_ORIGINS=["http://localhost:3000"]
```

### .env.local (프론트엔드 로컬)
```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### .env (프론트엔드 프로덕션)
- 비워둠 (상대 경로 사용)

## 모니터링

### 헬스체크
```bash
# API 서버
curl https://miniebook.vercel.app/api/health

# FlareSolverr
curl http://127.0.0.1:8191/health

# 챕터 API
curl https://miniebook.vercel.app/api/chapters/21431 | head -c 200
```

### 로그
- Vercel: Dashboard → Deployments → Logs
- FlareSolverr: `journalctl --user -u container-flaresolverr.service`

## 트러블슈팅

### EPUB 다운로드가 404
- 원인: novel_id가 DB 디렉토리에 없음
- 해결: `ls /opt/ai_data/flaresolverr/novels/`로 디렉토리 확인

### 챕터 본문이 비어있음
- 원인: 북토끼 502/522 에러
- 해결: bookto31.py fetch_chapter()로 재수집 후 DB 업데이트

### FlareSolverr "Container not found"
- 원인: svc.pod 정지
- 해결: `systemctl --user restart svc-pod container-flaresolverr`

### Port 80 already in use
- 원인: svc.pod가 80 PublishPort 포함
- 해결: svc.pod 정의에서 80/443 제거

## 다음 문서
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 유지보수 작업