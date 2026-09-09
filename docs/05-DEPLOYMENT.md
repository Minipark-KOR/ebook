# 배포 가이드

> ebooklib 시스템을 Vercel과 컨테이너 인프라에 배포하는 단계별 가이드.

## 환경 구성

### 호스팅
- **프론트엔드**: Vercel (Next.js, Root Directory = `apps/frontend`)
- **백엔드 API**: devforge (Oracle Cloud) — FastAPI @ `127.0.0.1:8089`, Caddy(nip.io)로 공개
- **FlareSolverr**: devforge 로컬 서버 (Podman Quadlet)
- **데이터 스토리지**: devforge 로컬 파일시스템 (`/opt/ai_data/`)

> Vercel의 `/api/*`는 `app/api/[...slug]/route.ts` catch-all이 **devforge 백엔드로 프록시**한다.
> (Neon DB 미사용 — 2026-09-09부터 단일 데이터 소스로 통일)

### 시스템 요구사항
- **devforge 서버** (FlareSolverr + FastAPI):
 - ARM64 또는 x86_64 Linux
 - Podman 4.x + systemd
 - 2GB RAM, 2 CPU core (FlareSolverr용)
 - ARM64 이미지: `ghcr.io/flaresolverr/flaresolverr:latest`
- **Vercel**:
 - Hobby ($0) 또는 Pro 계정

## Vercel 배포

### 1. 프로젝트 설정

**중요**: Vercel 프로젝트 생성 시 **Root Directory = `apps/frontend`**.

```
Project Settings:
  - Root Directory: apps/frontend
  - Framework Preset: Next.js
  - Build Command: npm run build
  - Output Directory: .next
```

`apps/frontend/vercel.json`:
```json
{
  "framework": "nextjs",
  "buildCommand": "npm run build",
  "outputDirectory": ".next"
}
```

### 2. 환경변수 설정

Vercel Dashboard → Settings → Environment Variables (production):

```
NEXT_PUBLIC_API_URL = https://devforge.152-69-229-246.nip.io   # devforge 백엔드
VERCEL_REVALIDATE_TOKEN = <revalidate 인증 토큰>
```

> `NEXT_PUBLIC_API_URL`은 빌드 시 베이크되므로 변경 시 재배포 필요.

### 3. 배포 명령

```bash
# Vercel CLI 설치
npm install -g vercel

# 최초 배포 (프로젝트 연결, Root Directory=apps/frontend 설정)
cd /opt/workspace/ebooklib
vercel --prod --project miniebook

# 이후 배포
vercel deploy --prod --project miniebook
```

## 백엔드(devforge) 배포

FastAPI 백엔드는 Vercel이 아니라 **devforge (Oracle Cloud)**에서 실행된다.

### 1. 서비스 실행

```bash
cd /opt/workspace/ebooklib/apps/backend
setsid nohup venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8089 \
  >> /var/tmp/ebook_backend.log 2>&1 < /dev/null &
```

### venv 필수 의존성 (toki31 수집용)

```bash
cd /opt/workspace/ebooklib/apps/backend
venv/bin/pip install playwright cryptography
```

> `playwright` + `cryptography`(AES-GCM)가 없으면 toki31 수집이 **즉시 실패**(body=0)한다.
> Playwright 브라우저 바이너리는 `~/.cache/ms-playwright`에 이미 존재.

### 2. 외부 노출 (Caddy)

- Caddy가 `devforge.152-69-229-246.nip.io` → `127.0.0.1:8089` 리버스 프록시
- Vercel의 `NEXT_PUBLIC_API_URL`이 이 도메인을 가리킴

### 3. 재시작 (코드 변경 반영)

```bash
# 프로세스 확인
ps aux | grep "port 8089"

# 재시작
kill <PID>
cd /opt/workspace/ebooklib/apps/backend
setsid nohup venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8089 \
  >> /var/tmp/ebook_backend.log 2>&1 < /dev/null &

# 검증
curl http://127.0.0.1:8089/api/novels | head -c 200
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

### .env (백엔드, devforge)
```env
ENV=development
DEBUG=true
CORS_ORIGINS=["https://miniebook.vercel.app"]
```

### .env.local (프론트엔드 로컬 개발)
```env
NEXT_PUBLIC_API_URL=http://127.0.0.1:8089   # devforge 백엔드 (또는 로컬 uvicorn)
```

### .env (프론트엔드 프로덕션)
- Vercel 대시보드에서 `NEXT_PUBLIC_API_URL` 설정 (빌드 시 베이크)

## 모니터링

### 헬스체크
```bash
# API 서버
curl https://miniebook.vercel.app/health

# FlareSolverr
curl http://127.0.0.1:8191/health

# 챕터 API
curl https://miniebook.vercel.app/api/chapters/21431 | head -c 200
```

### 로그
- Vercel: Dashboard → Deployments → Logs
- FlareSolverr: `journalctl --user -u container-flaresolverr.service`

## 자동화 시스템 배포 (pipeline loop)

파이프라인 상시 루프를 systemd로 실행한다. 상세는 [07-AUTOMATION.md](07-AUTOMATION.md) 참고.

### ebook-watcher.service (systemd)

`~/.config/systemd/user/ebook-watcher.service`:
```ini
[Unit]
Description=Ebook Pipeline — 파이프라인 루프 (devforge-watchdog 감시)
After=network-online.target svc-pod.service container-flaresolverr.service
Wants=network-online.target

[Service]
Type=notify                                                        # sd_notify (READY=1 + WATCHDOG=1)
EnvironmentFile=/home/opc/.config/devforge/secrets.env
WorkingDirectory=/opt/workspace/ebooklib
# 다중 소스: source 필터 없이 모든 소스 수집
ExecStart=/opt/workspace/ebooklib/apps/backend/venv/bin/python3 /opt/workspace/ebooklib/scripts/pipeline.py loop  # 다중 소스 (source 무관)
WatchdogSec=1800                                                   # 30분 내 신호 없으면 hang
Restart=on-watchdog                                                # hang/실패 시 재시작
RestartSec=30
StandardOutput=journal
StandardError=journal
```

> **소스 설정**: `apps/backend/sources.json`에서 수집 소스를 관리 (도메인/base_url/collector/discover/speed).
> 새 소스 추가 또는 도메인 변경은 이 파일만 수정하면 되고, 재시작이면 반영된다.

### 등록 명령
```bash
systemctl --user daemon-reload
systemctl --user enable --now ebook-watcher.service

# 상태 확인
systemctl --user status ebook-watcher.service
```

### devforge-watchdog 연동

`/opt/projects/server/scripts/lib/watchdog/config.py`의 `SERVICE_TARGETS`에 추가:

```python
SERVICE_TARGETS = [
    "devforge-turn-watcher",
    "ebook-watcher",  # ← 추가
]
```

devforge-watchdog이 60초마다 `check_ebook_pipeline()`(프로세스 + 로그 활동)으로 감시하고 죽으면 자동 재시작합니다.

### 큐 디렉토리 생성
```bash
mkdir -p /opt/ai_data/flaresolverr/ebook_watcher
```

(`pipeline.py loop` 최초 실행 시 자동 생성됨)

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

### ebook-watcher가 큐를 안 처리함
- 원인 1: 락 파일 stale (`worker.lock`이 30분 이상 남음)
 - 해결: `rm /opt/ai_data/flaresolverr/ebook_watcher/worker.lock`
- 원인 2: FlareSolverr 죽음
 - 해결: `curl http://127.0.0.1:8191/health` → `systemctl --user restart svc-pod container-flaresolverr`
- 원인 3: devforge-watchdog이 ebook-watcher 재시작 못함
 - 해결: `journalctl --user -u devforge-watchdog.service -n 50`

## 다음 문서
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 유지보수 작업
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템