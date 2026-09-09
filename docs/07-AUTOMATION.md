# 자동화 시스템

> 파이프라인 루프, devforge-watchdog, systemd 통합 자동화.

## 자동화 계층

```
devforge-watchdog @ 60초마다     ← SERVICE_TARGETS: ebook-watcher 체크 (프로세스+로그 활동)
ebook-watcher.service            ← Type=notify, WatchdogSec=1800, Restart=on-watchdog
  └─ pipeline.py loop            ← 상시 실행 (소스별 페이싱) + sd_notify 신호
```

**이중 감시 구조**:
1. **systemd WatchdogSec** (표준): loop이 각 사이클마다 `WATCHDOG=1` → 30분 내 미수신 시 hang 판정 → `on-watchdog` 재시작
2. **devforge-watchdog** (로컬): `check_ebook_pipeline()`이 프로세스 존재 + 마지막 로그 활동(20분) 확인 → hang/죽음 시 재시작

## 1. 파이프라인 루프 (pipeline.py loop)

**상시 실행**되는 프로세스로, 큐를 1개씩 소비하며 챕터를 수집합니다.

### 사이클 주기 (source별)

| source | 사이클 대기 | 챕터당 소요 |
|---|---|---|
| `bookto31` | 300초 (Cloudflare 보호) | 5~8분 |
| `toki31` | 5초 (내부 딜레이 스킵) | **15~30초** |

> **다중 소스**: 루프는 source 무관하게 모든 소스 수집. toki31은 유동 IP 회전으로 고속. 소스 추가/도메인 변경은 sources.json.

### 실행 흐름

```
1 Cycle:
  ├─ sd_notify (systemd watchdog 신호)
  ├─ collect (1개 챕터)
  │   ├─ bookto31: FlareSolverr → HTML → 본문 추출 (~12초)
  │   └─ toki31: Playwright + KR 프록시 → API 복호화 (~15~30초)
  ├─ EPUB 제작 훅 (해당 소설 queue가 비워진 경우)
  └─ 사이클 대기 (bookto31 300초 / toki31 5초)
```

### 시작 방법

```bash
# systemd 서비스로 (권장) — 현재 toki31 루프
systemctl --user start ebook-watcher.service

# CLI 직접 (NOTIFY_SOCKET 없으면 sd_notify 자동 무시)
python3 scripts/pipeline.py loop  # 다중 소스 (source 무관)
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

### ebook 전용 체크 (`check_ebook_pipeline`)

`checker.py`에 ebook-watcher 전용 liveness 체크가 있다. 일반 서비스(`svc_active`)와 달리
**프로세스 존재 + 마지막 로그 활동**까지 확인해 hang을 감지한다:

```python
# /opt/projects/server/scripts/lib/watchdog/checker.py
EBOOK_HANG_STALE_SEC = 1200  # 20분 이상 활동 없으면 hang 판정

def check_ebook_pipeline() -> tuple[bool, str]:
    # 1) systemd 서비스 active 여부
    # 2) `pipeline.py loop` 프로세스 존재 (pgrep)
    # 3) journal 마지막 Cycle/collect 로그 시간 → 20분 초과 시 hang
```

### 확인

```bash
# 서비스 상태
systemctl --user status ebook-watcher.service

# 최근 체크 로그
journalctl --user -u ebook-watcher.service -n 20

# devforge-watchdog 로그에서 ebook-watcher 확인
journalctl --user -u devforge-watchdog.service | grep -i ebook

# watchdog 전용 체크 직접 실행
python3 -c "
import sys; sys.path.insert(0, '/opt/projects/server/scripts')
from lib.watchdog.checker import check_ebook_pipeline
print(check_ebook_pipeline())
"
```

## 3. ebook-watcher.service (systemd)

```ini
# /home/opc/.config/systemd/user/ebook-watcher.service
[Unit]
Description=Ebook Pipeline — 파이프라인 루프 (devforge-watchdog 감시)
After=network-online.target svc-pod.service container-flaresolverr.service
Wants=network-online.target

[Service]
Type=notify                                                        # sd_notify (READY=1 + WATCHDOG=1)
EnvironmentFile=/home/opc/.config/devforge/secrets.env
WorkingDirectory=/opt/workspace/ebooklib
ExecStart=.../venv/bin/python3 .../scripts/pipeline.py loop  # 다중 소스 (source 무관)
WatchdogSec=1800                                                    # 30분 내 신호 없으면 hang
Restart=on-watchdog                                                # hang/실패 시 재시작
RestartSec=30                                                      # 30초 후 재시도
StandardOutput=journal
StandardError=journal
```

> **Watchdog 동작**: `pipeline.py loop`이 각 사이클마다 `_sd_notify()`로
> `WATCHDOG=1` 신호를 systemd에 보낸다. 30분(WatchdogSec) 내 신호가 없으면
> systemd가 프로세스를 hang으로 판단하고 `on-watchdog`으로 재시작한다.
> (기존 devforge-watchdog의 로그 기반 감지는 fallback으로 유지)

## 4. 안전 장치

### 챕터 수집 안전장치 (북토끼/뉴토끼 봇 탐지 회피)

| 장치 | 작동 |
|---|---|
| **챕터 간 적응형 지연** | 10×fetch 시간, bookto31 최소 5분 / toki31 5~60초 (소스별 speed_hint 기반 내부 딜레이로 페이싱) |
| **재시도 3회** | 같은 URL에 대한 빠른 반복 요청 방지 |
| **rate_limiter DB** | URL별 마지막 요청 시각 기록, 8분 + ±2분 jitter (bookto31) |
| **유동 IP 회전 (toki31)** | DataImpulse `__cr.kr` — 매 브라우저 세션 새 IP → IP 차단 무력화 |
| **FlareSolverr session 재사용** | 매번 새 세션 만들면 부담, 같은 세션으로 효율화 |
| **3회 실패 시 DLQ 기록** | failed.json에 보존 (데이터 손실 방지, 최대 5000개) |

### 시스템 보호 장치

| 장치 | 작동 |
|---|---|
| **systemd WatchdogSec + on-watchdog** | 30분 내 sd_notify 신호 없으면 hang 판정 후 재시작 |
| **devforge-watchdog 60초 체크** | 프로세스 + 로그 활동(20분) 확인, hang/죽음 시 재시작 |
| **queue 파일 락** | fcntl + atomic write, run_collect 단일 writer 직렬화 (동시 덮어쓰기 방지) |
| **Backoff schedule (CrashLoopBackOff)** | 반복 실패 시 0→10→20→40→80→120→300초 대기 |
| **namu_attempted 플래그** | namu.wiki 메타데이터 1회만 조회 (hang 방지) |

## 5. 큐 데이터 무결성 (fcntl 락)

`queue.json`은 여러 프로세스(bookto31 loop, toki31 collect)가 접근한다.
읽기-수정-쓰기 race로 서로의 항목을 덮어쓰는 버그를 방지하기 위해 **파일 잠금**을 사용한다.

### 락 파일 구성

| 파일 | 용도 |
|---|---|
| `queue.json.lock` | `_load_queue`/`_save_queue` 개별 접근 직렬화 (SH/EX) |
| `queue.json.collect.lock` | `run_collect` 전체 트랜잭션 직렬화 (EX) — **별도 파일** |

> **왜 별도 파일인가**: fcntl flock은 같은 프로세스에서 같은 fd에 두 번째 flock을 걸면
> **이전 락을 대체**한다. 만약 collect 락과 queue 락이 같은 파일이면, collect 락(EX) 보유 중
> `_load_queue`가 `LOCK_SH`를 걸어 collect 락이 다운그레이드/해제되는 버그가 발생한다.

### 쓰기 방식 (atomic write)

```python
# tmp 파일 (PID+스레드ID로 고유) → fsync → os.replace (원자적 rename)
tmp_path = QUEUE_FILE.with_suffix(f'.tmp.{os.getpid()}.{threading.get_ident()}')
json.dump(queue, f, ...)  # lock 보유 중
os.replace(tmp_path, QUEUE_FILE)
```

이중 안전장치:
1. **fcntl LOCK_EX**: 프로세스 간 동시 쓰기 직렬화 (멀티프로세스 검증: B가 A 해제까지 대기)
2. **atomic rename**: 중간 상태(부분 쓰기) 노출 방지 → JSON 손상 불가

## 6. DLQ (실패 항목 보존)

3회 재시도 후 실패한 챕터는 `queue.json`에서 제거되지만, 데이터 손실을 막기 위해
`failed.json`(DLQ)에 보존한다.

```json
{
  "wr_id": 5784900,
  "novel_title": "아포칼립스의 고인물",
  "source": "toki31",
  "chapter": 277,
  "error": "3회 시도 후 실패 (body=0)",
  "attempts": 3,
  "failed_at": "2026-09-09T02:00:00.000Z"
}
```

- 최대 5000개 유지 (무한 증가 방지)
- 사후 분석/재시도 가능 (`failed.json`에서 queue로 재삽입)
- 확인: `python3 -c "import json; print(len(json.load(open('/opt/ai_data/flaresolverr/ebook_watcher/failed.json'))))"`

## 7. 장애 복구 시나리오

| 상황 | 복구 | 시간 |
|------|------|------|
| 파이프라인 프로세스 hang (30분 무응답) | systemd WatchdogSec → on-watchdog 재시작 | 30분 |
| 파이프라인 프로세스 죽음 | systemd on-watchdog / devforge-watchdog 감지 | 30~60초 |
| 파이프라인 hang (로그 20분 없음) | devforge-watchdog 로그 기반 감지 | 20분 |
| 3회 실패 챕터 | DLQ(failed.json) 기록, 사후 재시도 가능 | 즉시 |
| FlareSolverr 다운 | FlareSolverrSession 재시도 (3회) | ~6초 |
| 북토끼 403 응답 | rate limiter 대기 후 재시도 | 8분 |
| namu.wiki hang | namu_attempted 플래그로 1회만 시도 | - |

## 8. 모니터링

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

## 9. 수동 제어

```bash
# 서비스 시작/중지/재시작
systemctl --user stop ebook-watcher.service
systemctl --user restart ebook-watcher.service

# 전체 로그 보기
journalctl --user -u ebook-watcher.service -f

# 파이프라인 직접 실행 (로그 보이게)
python3 scripts/pipeline.py loop "오늘만 사는 기사"
```

## 10. 파이프라인 시작 워크플로우

```
1. Admin 페이지 (https://miniebook.vercel.app/admin)
   └─ 비밀번호 + URL 입력
2. FastAPI /api/pipeline/start
   ├─ URL 자동 분기 (bookto31 / newtoki)
   ├─ 제목 자동 추출 (discover --dry-run)
   └─ discover → 큐 등록
3. ebook-watcher.service (pipeline.py loop)가 소스별 페이싱으로 수집
4. 챕터 저장 → index → revalidate → (다음 챕터)
```

## 11. 파이프라인 상태 확인

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