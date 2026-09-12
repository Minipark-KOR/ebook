# 유지보수 작업 가이드

> ebooklib 시스템을 운영하면서 자주 발생하는 작업과 해결책.

## 일반 작업

### 1. 신규 챕터 갱신

**권장**: ebook-watcher 자동화 시스템 사용 (수동 작업 불필요)

**자동 워크플로우**:
```bash
# 1. 작품 메인 wr_id로 discover → 큐 등록 (신규/누락 회차 발견)
python3 /opt/workspace/ebooklib/scripts/pipeline.py discover <main_wr_id> "소설 제목"

# 예: 하남자의 탑 공략법 (main_wr_id=21430)
python3 /opt/workspace/ebooklib/scripts/pipeline.py discover 21430 "하남자의 탑 공략법"

# 또는 Admin 페이지 (https://miniebook.vercel.app/admin)에서 URL 입력
# 또는 전체 체인 실행:
# python3 /opt/workspace/ebooklib/scripts/pipeline.py all <main_wr_id> "소설 제목"

# 자동 처리:
# - ebook-watcher.service (systemd)가 pipeline.py loop 상시 실행
# - systemd WatchdogSec(10분) + devforge-watchdog(60초) 이중 감시
# - discover로 큐에 등록된 회차를 collect → JSON 저장
# - index(인덱스 캐시) 재구축 → revalidate(Vercel ISR 갱신)
# - 3회 실패 시 DLQ(failed.json)에 보존 후 큐에서 제거
```

**수동 절차** (자동화 없이 직접 처리):
```bash
# 1. miniebook API로 신규 챕터 확인
curl -s "https://miniebook.vercel.app/api/novels/하남자의_탑_공략법/chapters?page=1&limit=1" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"Total: {d['pagination']['total']}\")
"

# 2. DB 현재 챕터 수 확인
ls /opt/ai_data/flaresolverr/novels/하남자의_탑_공략법/ | grep ".json$" | wc -l

# 3. 차이만큼 신규 wr_id 식별
# 예: API total=558, DB=557 → 1개 신규 (wr_id는 chapter 목록에서 확인)

# 4. 신규 챕터만 miniebook API로 가져오기
for wr_id in [...신규목록]:
    curl -s "https://miniebook.vercel.app/api/chapters/${wr_id}" > "${wr_id}.json"

# 5. DB에 저장
cp "${wr_id}.json" /opt/ai_data/flaresolverr/novels/하남자의_탑_공략법/

# 6. EPUB 재생성 (필요시)
curl -o updated.epub "https://miniebook.vercel.app/api/novels/.../epub"
```

**북토끼에서 직접 수집** (FlareSolverr 우회 필요):
```bash
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate

python3 << 'PYEOF'
import sys; sys.path.insert(0, '.')
from services.bookto31 import fetch_chapter, parse_chapter_body

wr_id = 21988
html = fetch_chapter(wr_id)  # rate_limit=True 자동
if html:
    body = parse_chapter_body(html)
    print(f"수집: {len(body)} chars")
PYEOF
```

### 2. EPUB 재생성 (로컬)

```bash
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python3 << 'PYEOF'
import sys; sys.path.insert(0, '.')
from services.epub import build_epub
data = build_epub("하남자의_탑_공략법")
with open("/tmp/하남자의_탑_공략법.epub", "wb") as f:
    f.write(data)
print(f"Size: {len(data):,} bytes")
PYEOF
```

### 3. 빈 챕터 감지 및 재수집

```python
# 빈 챕터 찾기
import os, json
db_dir = '/opt/ai_data/flaresolverr/novels/하남자의_탑_공략법'

empty = []
for f in os.listdir(db_dir):
    if f.endswith('.json') and f.split('.')[0].isdigit():
        with open(f"{db_dir}/{f}") as fp:
            d = json.load(fp)
        if not d.get('content') or len(d.get('content', '')) < 100:
            empty.append((int(f.split('.')[0]), d.get('title', '')))

print(f"빈 챕터: {len(empty)}개")
for wr_id, title in empty:
    print(f"  {wr_id}: {title}")

# 북토끼에서 재수집
import sys
sys.path.insert(0, '/opt/workspace/ebooklib/apps/backend')
from services.bookto31 import fetch_chapter, parse_chapter_body
import json as _json

for wr_id, _ in empty:
    html = fetch_chapter(wr_id)
    if html:
        body = parse_chapter_body(html)
        if body:
            with open(f"{db_dir}/{wr_id}.json") as fp:
                data = _json.load(fp)
            data['content'] = body
            data['content_length'] = len(body)
            with open(f"{db_dir}/{wr_id}.json", 'w') as fp:
                _json.dump(data, fp, ensure_ascii=False, indent=2)
            print(f"  ✓ {wr_id}: {len(body)} chars")
```

### 3-1. 중복 본문 / 챕터 번호 불일치 확인·수리

```bash
# 전체 소설 감지 (보고만)
python3 /opt/workspace/ebooklib/scripts/pipeline.py check-dupes

# 특정 소설 감지
python3 /opt/workspace/ebooklib/scripts/pipeline.py check-dupes 화산귀환

# 감지 + 자동 수리 (재정렬/중복 제거, 변경분 _dupe_backup_* 백업, 인덱스 재구축)
python3 /opt/workspace/ebooklib/scripts/pipeline.py check-dupes 화산귀환 --fix
```

- 감지 대상:
  - **동일 본문이 다른 화수(chapter 번호)로 중복 저장**된 경우 (예: 13921.json ↔ 12000.json)
  - **본문 표기("N화")와 저장 chapter 번호가 다른 경우** (소스 wr_id→화수 매핑 오프바이원)
- `--fix`: 본문 표기를 진실값으로 재정렬 + 중복 제거. 충돌(같은 화수를 다른 내용이 주장) 시 스킵+보고.
- collect 시에도 동일한 감지/더처가 자동 적용됨 (중복 저장 방지)

### 3-2. 누락 화수 추적/재처리

```bash
# 빠진 화수 감지 → missing.json 기록
python3 /opt/workspace/ebooklib/scripts/pipeline.py check-gaps [소설명]

# 누락/빈 챕터를 소스에서 재발견 → 큐 재등록 (정확한 wr_id)
python3 /opt/workspace/ebooklib/scripts/pipeline.py retry-missing [소설명] [--dry-run]
```

- `missing.json`에 `gap`(누락)/`empty_source`(사이트에 본문 없음) 유형으로 추적
- 월간 사이클이 자동으로 check-gaps 실행 (추적 갱신)
- **현재 소스에 없는 화수** (예: 화산귀환 1807, 1342/1345 / 바바리안 911)는
  **toki31(유료 프록시) 폴백**으로만 수집 가능 — `discover_toki31` 에피소드 맵으로 재큐 필요

## 데이터 작업

### 4. 챕터 직접 추가

```python
import json
import os

db_dir = '/opt/ai_data/flaresolverr/novels/{소설ID}'
os.makedirs(db_dir, exist_ok=True)

# 챕터 JSON 파일 생성
chapter_data = {
    "wr_id": 21431,
    "chapter": 1,
    "title": "소설 제목 - 1화",
    "content_length": 5804,
    "content": "본문 내용...",
    "url": "https://23.ondobook.net/bbs/board.php?bo_table=novel&wr_id=21431",
    "collected_at": "2026-09-05T00:00:00+09:00",
    "user_agent": "Mozilla/5.0 ..."
}

with open(f"{db_dir}/21431.json", 'w', encoding='utf-8') as f:
    json.dump(chapter_data, f, ensure_ascii=False, indent=2)
```

### 5. 새 소설 받기

**`meta.json` 필수**:
```json
{
  "id": "새소설ID",
  "title": "새 소설 제목",
  "author": "작가명",
  "totalChapters": 100,
  "coverUrl": null
}
```

**챕터 파일들** (`{wr_id}.json`): 위 4번 참조.

## FlareSolverr 작업

### 6. FlareSolverr 재시작

```bash
# 상태 확인
systemctl --user status svc-pod container-flaresolverr

# 재시작
systemctl --user restart svc-pod container-flaresolverr

# 헬스체크
sleep 5
curl http://127.0.0.1:8191/health
```

### 7. FlareSolverr standalone 모드 (svc.pod 실패 시)

```bash
# svc.pod가 80/443 충돌로 안 뜨면 standalone 실행
podman rm -f flaresolverr
podman run -d --name flaresolverr \
  -p 127.0.0.1:8191:8191 \
  -v /opt/ai_data/flaresolverr:/app/cache:Z \
  -e LOG_LEVEL=info \
  -e HEADLESS=true \
  -e DISABLE_MEDIA=true \
  -e BROWSER_WAIT_TIMEOUT=2 \
  ghcr.io/flaresolverr/flaresolverr:latest

sleep 10
curl http://127.0.0.1:8191/health
```

### 8. rate_limiter DB 정리

```bash
# 30일 이전 로그 삭제
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python3 -c "
from lib.rate_limiter import cleanup_old, stats
print(f'Before: {stats()}')
deleted = cleanup_old(days=30)
print(f'Deleted: {deleted}')
print(f'After: {stats()}')
"
```

## Git 작업

### 9. 변경사항 커밋

```bash
cd /opt/workspace/ebooklib
git status
git diff

# 커밋
git add <files>
git commit -m "feat: 설명"
git push origin main  # Vercel 자동 배포
```

### 10. Vercel 강제 재배포

```bash
# 빈 커밋으로 트리거
git commit --allow-empty -m "chore: trigger redeploy"
git push origin main
```

## 문제 해결

### 11. API가 404 반환

**원인**: novel_id가 DB에 없음

**진단**:
```bash
ls /opt/ai_data/flaresolverr/novels/
curl -s "https://miniebook.vercel.app/api/novels/$(python3 -c 'import urllib.parse; print(urllib.parse.quote("하남자의_탑_공략법"))')" | python3 -m json.tool
```

**해결**: 소설 디렉토리 생성 + meta.json + 챕터 JSON 추가

### 12. 챕터 본문이 빈 값으로 표시

**원인**: 북토끼 502/522 에러 또는 빈 content 저장

**진단**:
```bash
# 빈 챕터 확인
python3 << 'PYEOF'
import os, json
db_dir = '/opt/ai_data/flaresolverr/novels/하남자의_탑_공략법'
empty = []
for f in os.listdir(db_dir):
    if f.endswith('.json') and f.split('.')[0].isdigit():
        with open(f"{db_dir}/{f}") as fp:
            d = json.load(fp)
        if not d.get('content') or len(d.get('content', '')) < 100:
            empty.append((int(f.split('.')[0]), d.get('title', '')))
print(f"빈 챕터: {len(empty)}개")
for w, t in empty: print(f"  {w}: {t}")
PYEOF
```

**해결**: 위 3번 절차로 재수집.

### 13. EPUB 다운로드 500 에러

**원인**: 챕터 데이터 부족

**진단**:
```bash
ls /opt/ai_data/flaresolverr/novels/{소설ID}/ | grep ".json$" | wc -l
```

**해결**: meta.json 또는 챕터 파일 추가

### 14. FlareSolverr "Challenge failed"

**원인**: Cloudflare가 FlareSolverr 패턴 탐지

**해결**:
1. 잠시 대기 (5-10분)
2. 다른 브라우저 모드로 시도 (FlareSolverr 옵션)
3. session 재사용 (같은 챕터 일괄 시)

### 15. svc.pod 80/443 충돌

**증상**:
```
Error: starting container: rootlessport listen tcp 0.0.0.0:80: bind: address already in use
```

**해결**: `~/.config/containers/systemd/svc.pod`에서 80/443 PublishPort 제거.

## 백업 및 복원

### 16. 챕터 데이터 백업

```bash
# 백업
tar -czf ebooklib-backup-$(date +%Y%m%d).tar.gz \
  /opt/ai_data/flaresolverr/novels/

# 복원
tar -xzf ebooklib-backup-YYYYMMDD.tar.gz -C /
```

### 17. GitHub 백업

ebooklib repo는 자동으로 GitHub에 백업됨 (`Minipark-KOR/ebook`).

```bash
cd /opt/workspace/ebooklib
git log --oneline -10
git remote -v
```

## 자동화 시스템 (pipeline loop) 작업

### 18. 큐 관리 CLI

```bash
# 신규/누락 회차 discover (큐에 등록)
python3 /opt/workspace/ebooklib/scripts/pipeline.py discover <main_wr_id> "소설 제목"

# 전체 체인 (discover → collect → enrich → index → revalidate)
python3 /opt/workspace/ebooklib/scripts/pipeline.py all <main_wr_id> "소설 제목"

# collect 단계만 (큐 처리)
python3 /opt/workspace/ebooklib/scripts/pipeline.py collect --limit 5 --source bookto31

# 큐 상태
cat /opt/ai_data/flaresolverr/ebook_watcher/queue.json | python3 -m json.tool
```

### 19. 파이프라인 루프 상태 확인

```bash
# 서비스 상태 (Type=notify + WatchdogSec=600)
systemctl --user status ebook-watcher.service

# 실시간 로그
journalctl --user -u ebook-watcher.service -f

# 파이프라인 로그 직접
tail -f /opt/ai_data/flaresolverr/ebook_watcher/pipeline_output.log

# 수집 진행/상태
cat /opt/ai_data/flaresolverr/ebook_watcher/status.json | python3 -m json.tool

# 실패(DLQ) 확인
cat /opt/ai_data/flaresolverr/ebook_watcher/failed.json | python3 -m json.tool
```

### 20. collect 수동 실행 (테스트)

```bash
# 큐의 일부를 즉시 처리 (안전 지연 무시)
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python3 /opt/workspace/ebooklib/scripts/pipeline.py collect --limit 1 --source bookto31
```

### 21. devforge-watchdog 통합 확인

```bash
# ebook-watcher가 워치독의 SERVICE_TARGETS에 등록되었는지 확인
grep -A3 "SERVICE_TARGETS" /opt/projects/server/scripts/lib/watchdog/config.py

# 워치독이 ebook-watcher를 자동 재시작한 이력
journalctl --user -u devforge-watchdog.service | grep -i "ebook"
```

### 22. 자동화 시스템 트러블슈팅

**증상**: 큐에 작업이 있는데 ebook-watcher가 안 돌음

```bash
# 1. 락 파일 stale 확인 (30분 이상)
ls -la /opt/ai_data/flaresolverr/ebook_watcher/worker.lock

# 락이 stale이면 제거
rm /opt/ai_data/flaresolverr/ebook_watcher/worker.lock

# 2. FlareSolverr 상태
curl -s http://127.0.0.1:8191/health

# 죽었으면 재시작
systemctl --user restart svc-pod container-flaresolverr

# 3. ebook-watcher 자체 재시작
systemctl --user restart ebook-watcher.service

# 4. 워치독이 ebook-watcher를 죽었다고 판단하지 않는지 확인
journalctl --user -u devforge-watchdog.service --since "10 min ago" | grep -i ebook
```

### DLQ (실패 챕터) 재시도

3회 시도 후 실패한 챕터는 `queue.json`에서 제거되고 `failed.json`(DLQ)에 보존된다.

```bash
# DLQ 확인
python3 -c "
import json
with open('/opt/ai_data/flaresolverr/ebook_watcher/failed.json') as f:
    failed = json.load(f)
print(f'실패 챕터: {len(failed)}개')
for item in failed[-5:]:
    print(f\"  {item.get('novel_title')} wr_id={item.get('wr_id')} err={item.get('error')}\")
"
```

**DLQ → 큐 재삽입 (재시도)**:

```bash
python3 -c "
import json
failed = json.load(open('/opt/ai_data/flaresolverr/ebook_watcher/failed.json'))
queue = json.load(open('/opt/ai_data/flaresolverr/ebook_watcher/queue.json'))
existing = {item['wr_id'] for item in queue}
reinserted = 0
for item in failed:
    if item['wr_id'] not in existing:
        queue.append({
            'wr_id': item['wr_id'],
            'novel_title': item['novel_title'],
            'chapter': item.get('chapter'),
            'source': item.get('source', 'bookto31'),
            'priority': 1,
            'added_at': '2026-01-01T00:00:00+00:00',
            'attempts': 0,
            'last_error': None,
        })
        existing.add(item['wr_id'])
        reinserted += 1
json.dump(queue, open('/opt/ai_data/flaresolverr/ebook_watcher/queue.json', 'w'), ensure_ascii=False, indent=2)
print(f'{reinserted}개 재삽입')
"
```

### queue 파일 락 확인

`queue.json`은 fcntl 락(`queue.lock`, `queue.collect.lock`) + atomic write로
다중 프로세스 동시 접근 시 race를 방지한다. 락 파일이 정상 생성됐는지 확인:

```bash
ls -la /opt/ai_data/flaresolverr/ebook_watcher/*.lock
```

> 참고: fcntl은 **프로세스 단위 락**이라, 같은 프로세스 내 스레드 간에는 직렬화하지 않는다.
> 이 시스템은 프로세스 단위(bookto31 loop / toki31 collect)로 운영되므로 정상 동작한다.

## 다음 문서
- [00-ARCHITECTURE.md](00-ARCHITECTURE.md) - 시스템 전체 이해
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템 상세