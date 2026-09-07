# 자동화 시스템

> 파이프라인 루프, devforge-watchdog, systemd 통합 자동화.

## 자동화 계층

```
devforge-watchdog @ 60초마다     ← SERVICE_TARGETS: ebook-watcher.service 체크
ebook-watcher.service            ← Type=simple, Restart=on-failure (30초)
  └─ pipeline.py loop            ← 상시 실행 (5분 간격)
```

## 1. 파이프라인 루프 (pipeline.py loop)

**상시 실행**되는 프로세스로, 5분 간격으로 챕터를 수집합니다.

### 실행 흐름

```
1 Cycle (5분):
  ├─ collect (1개 챕터)
  │   ├─ bookto31: FlareSolverr → HTML → 본문 추출 (~12초)
  │   └─ newtoki: Playwright → 복호화 (~30초)
  ├─ index (_chapters_index.json 재구축)
  ├─ revalidate (Vercel ISR 캐시 갱신)
  └─ 300초 대기
```

### 시작 방법

```bash
# systemd 서비스로 (권장)
systemctl --user start ebook-watcher.service

# CLI 직접
python3 scripts/pipeline.py loop "오늘만 사는 기사"
```

## 2. devforge-watchdog

**60초마다** `SERVICE_TARGETS`에 등록된 서비스의 상태를 체크하고 죽으면 재시작합니다.

### 등록 상태

```python
# /opt/projects/server/scripts/lib/watchdog/config.py
SERVICE_TARGETS = [
    "devforge-turn-watcher",
    "ebook-watcher",       # ← 등록 완료
]
```

### 확인

```bash
# 서비스 상태
systemctl --user status ebook-watcher.service

# 최근 체크 로그
journalctl --user -u ebook-watcher.service -n 20

# devforge-watchdog 로그에서 ebook-watcher 확인
journalctl --user -u devforge-watchdog.service | grep -i ebook
```

## 3. ebook-watcher.service (systemd)

```ini
# /home/opc/.config/systemd/user/ebook-watcher.service
[Unit]
Description=Ebook Pipeline — 파이프라인 루프 (devforge-watchdog 감시)
After=network-online.target svc-pod.service container-flaresolverr.service
Wants=network-online.target

[Service]
Type=simple                                                        # 상시 실행
EnvironmentFile=/home/opc/.config/devforge/secrets.env
WorkingDirectory=/opt/workspace/ebooklib
ExecStart=.../venv/bin/python3 .../scripts/pipeline.py loop "오늘만 사는 기사"
Restart=on-failure                                                 # 실패 시 재시작
RestartSec=30                                                      # 30초 후 재시도
StandardOutput=journal
StandardError=journal
```

## 4. 안전 장치

### 챕터 수집 안전장치 (북토끼/뉴토끼 봇 탐지 회피)

| 장치 | 작동 |
|---|---|
| **챕터 간 5분 지연** | 사이트가 짧은 시간 내 반복 요청 시 의심 |
| **재시도 3회 (8분 간격)** | 같은 URL에 대한 빠른 반복 요청 방지 |
| **rate_limiter DB** | URL별 마지막 요청 시각 기록, 8분 + ±2분 jitter |
| **FlareSolverr session 재사용** | 매번 새 세션 만들면 부담, 같은 세션으로 효율화 |
| **3회 실패 시 큐 제거** | 영구 실패 챕터는 큐에서 자동 제거 |

### 시스템 보호 장치

| 장치 | 작동 |
|---|---|
| **systemd Restart=on-failure** | 파이프라인 프로세스 죽으면 30초 후 자동 재시작 |
| **devforge-watchdog 60초 체크** | systemd 서비스까지 죽으면 강제 재시작 |
| **Backoff schedule (CrashLoopBackOff)** | 반복 실패 시 0→10→20→40→80→120→300초 대기 |
| **namu_attempted 플래그** | namu.wiki 메타데이터 1회만 조회 (hang 방지) |

## 5. 장애 복구 시나리오

| 상황 | 복구 | 시간 |
|------|------|------|
| 파이프라인 프로세스 죽음 | systemd Restart=on-failure | 30초 |
| systemd 서비스 멈춤 | devforge-watchdog 감지 | 60초 |
| FlareSolverr 다운 | FlareSolverrSession 재시도 (3회) | ~6초 |
| 북토끼 403 응답 | rate limiter 대기 후 재시도 | 8분 |
| namu.wiki hang | namu_attempted 플래그로 1회만 시도 | - |

## 6. 모니터링

```bash
# 파이프라인 상태
journalctl --user -u ebook-watcher.service --since "10 min ago"

# 큐 상태
python3 -c "
import json
q = json.load(open('/opt/ai_data/flaresolverr/ebook_watcher/queue.json'))
print(f'큐: {len(q)}개')
"

# 저장된 챕터 수
ls /opt/ai_data/flaresolverr/novels/*/*.json | wc -l

# 워치독 확인
systemctl --user status devforge-watchdog.service --no-pager | head -10
```

## 7. 수동 제어

```bash
# 서비스 시작/중지/재시작
systemctl --user stop ebook-watcher.service
systemctl --user restart ebook-watcher.service

# 전체 로그 보기
journalctl --user -u ebook-watcher.service -f

# 파이프라인 직접 실행 (로그 보이게)
python3 scripts/pipeline.py loop "오늘만 사는 기사"
```

## 8. 파이프라인 시작 워크플로우

```
1. Admin 페이지 (https://miniebook.vercel.app/admin)
   └─ 비밀번호 + URL 입력
2. FastAPI /api/pipeline/start
   ├─ URL 자동 분기 (bookto31 / newtoki)
   ├─ 제목 자동 추출 (discover --dry-run)
   └─ discover → 큐 등록
3. ebook-watcher.service (pipeline.py loop)가 5분 간격으로 수집
4. 챕터 저장 → index → revalidate → (다음 챕터)
```

## 9. 파이프라인 상태 확인

```bash
# FastAPI 상태 API
curl http://localhost:8089/api/pipeline/status

# 큐 소스별 분포
python3 -c "
import json
from collections import Counter
q = json.load(open('/opt/ai_data/flaresolverr/ebook_watcher/queue.json'))
print(Counter(item.get('source','bookto31') for item in q))
"
```

## 관련 문서
- [00-ARCHITECTURE.md](00-ARCHITECTURE.md) - 시스템 전체 아키텍처
- [01-DATA-PIPELINE.md](01-DATA-PIPELINE.md) - 데이터 흐름
- [05-DEPLOYMENT.md](05-DEPLOYMENT.md) - 배포
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 운영 작업 가이드