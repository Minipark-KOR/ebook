# 자동화 시스템 (Automation)

> ebook-watcher, devforge-watchdog, systemd 타이머 통합 자동화 시스템.

## 개요

ebooklib 시스템은 **3중 보호 계층**으로 자동 운영됩니다:

```
[ Layer 3: ebook-watcher.timer @ *:0/15 ] ← 15분마다 워커 트리거
         ↓
[ Layer 2: ebook-watcher.service ]         ← 워커 실행 (큐 처리)
         ↓ 죽으면
[ Layer 1: devforge-watchdog @ 60초마다 ]  ← 서비스 상태 체크 + 자동 재시작
         ↓ 죽으면
[ Layer 0: systemd timer (*:0/15) ]         ← 15분마다 워커 트리거 (Type=oneshot, Restart=no)
```

## 컴포넌트

### ebook-watcher (15분 그룹)

**위치**: `/opt/workspace/ebooklib/scripts/ebook_watcher/`

**파일**:
- `watchdog.py` - 메인 트리거. timer(15분)가 호출 시 큐 체크 후 워커 실행
- `ebook_worker.py` - 큐 작업을 실제로 처리 (북토끼 챕터 수집)
- `ebook_queue.py` - CLI 큐 관리 도구

**systemd 서비스**:
- `ebook-watcher.service` - 워커 프로세스 (Type=oneshot, Restart=no, timer 트리거 전용)
- `ebook-watcher.timer` - `OnCalendar=*:0/15` 패턴, 15분마다 트리거

**큐 디렉토리**: `/opt/ai_data/flaresolverr/ebook_watcher/`
- `queue.json` - 작업 큐 (wr_id, novel_title, priority, attempts)
- `status.json` - 마지막 실행 결과
- `worker.lock` - 동시 실행 방지 락 (30분 stale 자동 정리)
- `watcher.log` - 실행 로그

### devforge-watchdog (60초 그룹)

**위치**: `/opt/projects/server/scripts/lib/watchdog/`

**모듈**:
- `orchestrator.py` - 메인 루프, 액션 컨슈머
- `checker.py` - 헬스 체크 (서비스/LLM/타이머)
- `fixloop.py` - LLM 기반 자동 수정
- `recovery.py` - 단계적 복구 (서비스 재시작, OOM kill)
- `notifier.py` - Slack/Opsgenie 알림
- `state.py` - 상태 관리, 트렌드 분석
- `messenger.py` - Slack heartbeat
- `codescanner.py` - silent-catch 스캔
- `config.py` - 모든 상수와 스케줄

**systemd 서비스**:
- `devforge-watchdog.service` - 상시 실행 (Type=simple, Restart=always, RestartSec=10)

**설정 파일**: `/opt/projects/server/scripts/lib/watchdog/config.py`

### ebook-watcher를 devforge-watchdog에 등록

`config.py`:
```python
SERVICE_TARGETS = [
    "devforge-turn-watcher",
    "ebook-watcher",  # ← 추가
]
```

이렇게 하면 devforge-watchdog이 60초마다 ebook-watcher 서비스 상태를 확인하고 죽으면 자동 재시작합니다.

## 동작 흐름

### 정상 흐름

```
1. ebook-watcher.timer @ 06:00, 06:15, 06:30, ...
   ↓ 트리거
2. ebook-watcher.service 실행 (watchdog.py)
   ├─ acquire_lock() - 락 파일 생성
   ├─ process_queue() - 큐의 모든 챕터 처리
   │   ├─ 챕터 사이 5분 안전 지연 (북토끼 탐지 회피)
   │   ├─ 재시도 3회 (8분 간격)
   │   └─ 실패 시 attempts 증가, 5회까지 큐 유지
   ├─ update_status() - status.json 갱신
   └─ release_lock() - 락 해제
3. 서비스 종료 (다음 timer까지 대기)
```

### ebook-watcher 죽었을 때 (Layer 1 작동)

```
1. ebook-watcher.service가 실행 중 죽음 (예: 메모리 부족)
2. ebook-watcher.service는 Type=oneshot이므로 systemd가 재시작하지 않음
3. ebook-watcher.timer @ 다음 15분마다 트리거
   → systemd가 ebook-watcher.service 다시 시작
4. 그래도 15분 내에 복구되지 않으면
   devforge-watchdog이 60초 체크 후 detect
   → recover_service() 호출
   → systemctl --user restart ebook-watcher
```

### 큐 워커가 죽었을 때 (lock 파일)

```
1. ebook_worker.py가 도중 죽음 (예: 북토끼 timeout)
2. lock 파일이 남음
3. 다음 timer 트리거 → watchdog.py 시작
4. acquire_lock() → is_lock_held() 체크
5. 락이 30분 이상 오래됐으면 stale로 간주 → 제거 → 새 워커 시작
6. 락이 30분 이내면 → 대기 (이미 다른 워커 돌고 있다고 판단)
```

## 안전 장치

### 챕터 수집 안전장치 (북토끼 봇 탐지 회피)

| 장치 | 작동 |
|---|---|
| **챕터 간 5분 지연** | 북토끼가 짧은 시간 내 다른 페이지 요청 시 의심 |
| **재시도 3회 (8분 간격)** | 같은 URL에 대한 빠른 반복 요청 방지 |
| **rate_limiter DB** | URL별 마지막 요청 시각 기록, 8분 + ±2분 jitter |
| **FlareSolverr session 재사용** | 매번 새 세션 만들면 부담, 같은 세션으로 효율화 |
| **5회 실패 시 큐 제거** | 영구 실패 챕터는 큐에서 자동 제거 |

### 시스템 보호 장치

| 장치 | 작동 |
|---|---|
| **systemd timer (15분)** | ebook-watcher.service는 Type=oneshot, timer가 15분마다 트리거 |
| **devforge-watchdog 60초 체크** | 서비스 죽으면 강제 재시작 |
| **Backoff schedule (CrashLoopBackOff)** | 반복 실패 시 0→10→20→40→80→120→300초 대기 |
| **Circuit breaker** | 120초 후 HALF_OPEN 시도 |
| **Experiment active 시 재시작 skip** | 실험 중일 때 서비스 재시작 방지로 데이터 손상 방지 |

## 운영 명령

### 큐 관리

```bash
# 챕터 추가
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add <wr_id> "소설 제목"

# 큐 상태 확인
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py list

# 특정 챕터 제거
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py remove <wr_id>

# 상태 (마지막 실행 결과)
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py status

# 큐 전체 비우기
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py clear
```

### 시스템 서비스 관리

```bash
# ebook 워처 타이머
systemctl --user status ebook-watcher.timer
systemctl --user list-timers ebook-watcher.timer
systemctl --user disable ebook-watcher.timer  # 비활성화

# ebook 워처 서비스
systemctl --user status ebook-watcher.service
journalctl --user -u ebook-watcher.service -f  # 실시간 로그

# 워치독
systemctl --user status devforge-watchdog.service
journalctl --user -u devforge-watchdog.service -f

# ebook 워처 로그 직접
tail -f /opt/ai_data/flaresolverr/ebook_watcher/watcher.log
```

### 테스트 (수동 워커 실행)

```bash
# 큐의 모든 작업을 즉시 처리 (안전 지연 무시, 테스트용)
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_worker.py
```

## 신규 챕터 추가 워크플로우

```
1. 관리자가 CLI로 wr_id 추가
   $ python3 ebook_queue.py add 25575 "오늘만 사는 기사"

2. 큐 파일에 추가됨
   /opt/ai_data/flaresolverr/ebook_watcher/queue.json
   [
     {"wr_id": 25575, "novel_title": "오늘만 사는 기사", "priority": 5, ...},
     ...
   ]

3. 다음 ebook-watcher.timer 트리거 (최대 15분 대기) 또는 수동 실행

4. ebook_watcher 워커가 큐 처리:
   - 5분 챕터 간 안전 지연
   - 북토끼에서 fetch (FlareSolverr 우회)
   - DB에 저장
   - 큐에서 제거

5. 실패 시 attempts 증가, 5회까지 큐 유지, 6회 시 자동 제거

6. status.json에 결과 저장:
   {"last_run": "...", "processed": 3, "errors": [], "remaining": 0}
```

## 모니터링 메트릭

- **처리량**: 큐 작업이 15분마다 실행되므로 시간당 ~4 챕터 (5분 간격)
- **복구 시간**: 서비스 죽으면 60초 이내 자동 재시작
- **메모리**: ebook-watcher 평소 ~30MB, 워커 실행 중 ~50MB
- **로그 위치**: `/opt/ai_data/flaresolverr/ebook_watcher/watcher.log`

## 트러블슈팅

### 큐가 안 줄어듦 (작업이 안 끝남)
1. 로그 확인: `tail -f /opt/ai_data/flaresolverr/ebook_watcher/watcher.log`
2. FlareSolverr 상태: `curl http://127.0.0.1:8191/health`
3. svc.pod 상태: `systemctl --user status svc-pod`
4. 락 파일 stale 확인: `ls -la /opt/ai_data/flaresolverr/ebook_watcher/worker.lock`

### ebook-watcher가 계속 죽음
1. 워치독이 복구 시도 로그 확인: `journalctl -u devforge-watchdog`
2. ebook-watcher 직접 실행해서 에러 메시지 확인
3. 메모리/CPU 확인: `systemctl --user status ebook-watcher.service`

### 워치독이 ebook-watcher를 재시작 못함
1. ebook-watcher.service가 dis 활성화됐는지: `systemctl --user is-enabled ebook-watcher.service`
2. 워치독 로그: `journalctl -u devforge-watchdog -n 100`

## 다음 단계 통합

- **TIMER_TARGETS 추가**: `ebook-watcher.timer`도 워치독이 주기 검증
- **Slack 알림**: 큐 실패 시 워치독이 Slack 메시지 전송
- **상태 대시보드**: status.json 기반으로 Grafana 등 시각화

## 관련 문서
- [00-ARCHITECTURE.md](00-ARCHITECTURE.md) - 시스템 전체 아키텍처
- [05-DEPLOYMENT.md](05-DEPLOYMENT.md) - 배포 (systemd 등록 부분)
- [06-MAINTENANCE.md](06-MAINTENANCE.md) - 운영 작업 가이드
## 듀얼 SSOT (북토끼 다운 대비)

북토끼는 본문 크롤러, 메타데이터는 문피아/조아라에서 가져옴.

### 역할 분리
| 출처 | 역할 | 도구 |
|---|---|---|
| **북토끼** | 챕터 본문 | `services/bookto31.py` + FlareSolverr |
| **문피아/조아라** | 메타데이터 SSOT | `scripts/dual_metadata_ssot.py` (Brave Search로 URL 검색) |
| **namu.wiki** | 표지 이미지 백업 | `services/metadata_namu.py` |

### 북토끼 health check
- `scripts/bookto31_healthcheck.py` - 독립 CLI (북토끼 + 조아라/문피아 응답 확인)
- `ebook_worker.py:_check_bookto31_alive()` - 10분 캐시로 15분 사이클마다 자동 호출
- 죽으면 ebook-watcher가 자동 중단 (recover 안 함, 수동 개입 대기)

### 자동 발견 + 수집 파이프라인
1. **수동 또는 Brave Search**: `scripts/discover_chapters.py` - 북토끼 작품 메인 페이지에서 spage 순회, 회차 wr_id 자동 추출 → 큐에 일괄 추가
2. **ebook-watcher**: 15분마다 큐의 챕터를 북토끼에서 fetch
3. **북토끼 죽음 시**: ebook-watcher가 자동 중단 + 다음 수동 작업 가능
   - `scripts/discover_chapters.py`의 munpia/joara URL로 직접 fetch
   - Brave Search로 다른 출처 찾기

