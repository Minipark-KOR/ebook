#!/usr/bin/env python3
"""ebooklib 워처 큐 관리 CLI.

사용법:
  ebook_queue.py add <wr_id> [novel_title]
  ebook_queue.py list
  ebook_queue.py remove <wr_id>
  ebook_queue.py status
  ebook_queue.py clear
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '/opt/workspace/ebooklib/scripts/ebook_watcher')

WATCHER_DIR = Path('/opt/ai_data/flaresolverr/ebook_watcher')
WATCHER_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_FILE = WATCHER_DIR / 'queue.json'
STATUS_FILE = WATCHER_DIR / 'status.json'


def load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_queue(queue: list) -> None:
    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def cmd_add(wr_id: int, novel_title: str = "", priority: int = 5) -> None:
    """큐에 챕터 추가."""
    queue = load_queue()
    if any(item['wr_id'] == wr_id for item in queue):
        print(f"❌ wr_id={wr_id} 이미 큐에 있음")
        return

    queue.append({
        "wr_id": wr_id,
        "novel_title": novel_title,
        "priority": priority,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "attempts": 0,
        "last_error": None,
    })
    save_queue(queue)
    print(f"✓ wr_id={wr_id} 추가됨: {novel_title}")


def cmd_list() -> None:
    """큐 목록 출력."""
    queue = load_queue()
    if not queue:
        print("큐 비어 있음")
        return

    print(f"=== 큐 ({len(queue)}개) ===")
    for item in queue:
        attempts = item.get('attempts', 0)
        err = item.get('last_error', '')
        status = "✓" if attempts == 0 and not err else f"⚠️ 시도 {attempts}/5"
        print(f"  [{status}] wr_id={item['wr_id']} | {item.get('novel_title', '')} | "
              f"추가: {item.get('added_at', '')[:19]}")
        if err:
            print(f"        에러: {err}")


def cmd_remove(wr_id: int) -> None:
    """큐에서 제거."""
    queue = load_queue()
    new_queue = [item for item in queue if item['wr_id'] != wr_id]
    if len(new_queue) == len(queue):
        print(f"❌ wr_id={wr_id} 큐에 없음")
    else:
        save_queue(new_queue)
        print(f"✓ wr_id={wr_id} 제거됨")


def cmd_status() -> None:
    """상태 파일 출력."""
    if not STATUS_FILE.exists():
        print("상태 파일 없음")
        return
    with open(STATUS_FILE, 'r', encoding='utf-8') as f:
        status = json.load(f)
    print("=== 상태 ===")
    for k, v in status.items():
        if k == "errors" and isinstance(v, list):
            print(f"  {k}: {len(v)}개")
            for e in v[:5]:
                print(f"    {e}")
        else:
            print(f"  {k}: {v}")


def cmd_clear() -> None:
    """큐 비우기."""
    save_queue([])
    print("✓ 큐 비움")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "add":
        if len(args) < 1:
            print("사용법: add <wr_id> [novel_title] [priority]")
            return 1
        wr_id = int(args[0])
        title = args[1] if len(args) > 1 else ""
        priority = int(args[2]) if len(args) > 2 else 5
        cmd_add(wr_id, title, priority)
    elif cmd == "list":
        cmd_list()
    elif cmd == "remove":
        if len(args) < 1:
            print("사용법: remove <wr_id>")
            return 1
        cmd_remove(int(args[0]))
    elif cmd == "status":
        cmd_status()
    elif cmd == "clear":
        cmd_clear()
    else:
        print(f"❌ 알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())