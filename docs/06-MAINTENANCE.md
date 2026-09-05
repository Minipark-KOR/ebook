# 유지보수 작업 가이드

> ebooklib 시스템을 운영하면서 자주 발생하는 작업과 해결책.

## 일반 작업

### 1. 신규 챕터 갱신

**권장**: ebook-watcher 자동화 시스템 사용 (수동 작업 불필요)

**자동 워크플로우**:
```bash
# 1. 큐에 새 챕터 추가
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add <wr_id> "소설 제목"

# 예: 하남자의 탑 공략법 새 회차
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add 21988 "하남자의 탑 공략법"

# 2. (선택) 우선순위 지정 - 낮을수록 먼저 처리
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add 21988 "하남자의 탑 공략법" 1

# 3. 큐 확인
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py list

# 자동 처리:
# - ebook-watcher.timer @ *:0/15 → 워커 실행
# - 북토끼에서 fetch (5분 챕터 간 안전 지연, 재시도 3회)
# - DB에 저장
# - status.json에 결과 기록
# - 5회 실패 시 큐에서 자동 제거
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
    "url": "https://bookto31.com/bbs/board.php?bo_table=novel&wr_id=21431",
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

## 자동화 시스템 (ebook-watcher) 작업

### 18. 큐 관리 CLI

```bash
# 챕터 추가
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add <wr_id> "소설 제목"

# 우선순위와 함께 (낮을수록 먼저)
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py add 21988 "제목" 1

# 큐 목록 보기
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py list

# 상태 (마지막 실행 결과)
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py status

# 특정 챕터 제거
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py remove <wr_id>

# 큐 전체 비우기 (주의)
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_queue.py clear
```

### 19. 워처/워커 상태 확인

```bash
# 타이머 상태 (다음 실행 시각)
systemctl --user status ebook-watcher.timer
systemctl --user list-timers ebook-watcher.timer

# 서비스 상태
systemctl --user status ebook-watcher.service

# 실시간 로그
journalctl --user -u ebook-watcher.service -f

# 워커 로그 직접
tail -f /opt/ai_data/flaresolverr/ebook_watcher/watcher.log

# 락 파일 ( stale 체크)
ls -la /opt/ai_data/flaresolverr/ebook_watcher/worker.lock

# 큐 + 상태
cat /opt/ai_data/flaresolverr/ebook_watcher/queue.json | python3 -m json.tool
cat /opt/ai_data/flaresolverr/ebook_watcher/status.json | python3 -m json.tool
```

### 20. 워커 수동 실행 (테스트)

```bash
# 큐의 모든 작업을 즉시 처리 (안전 지연 무시)
cd /opt/workspace/ebooklib/apps/backend
source venv/bin/activate
python3 /opt/workspace/ebooklib/scripts/ebook_watcher/ebook_worker.py
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

**증상**: 5회 실패한 챕터가 큐에 계속 남아있음 (정상 동작)

```bash
# 큐 확인 - attempts가 5 이상이고 last_error가 있으면 자동 제거됨
python3 -c "
import json
with open('/opt/ai_data/flaresolverr/ebook_watcher/queue.json') as f:
    queue = json.load(f)
for item in queue:
    if item.get('attempts', 0) >= 5:
        print(f\"영구 실패: {item}\")
"
```

## 다음 문서
- [00-ARCHITECTURE.md](00-ARCHITECTURE.md) - 시스템 전체 이해
- [07-AUTOMATION.md](07-AUTOMATION.md) - 자동화 시스템 상세