from typing import Any, Optional, cast
# -*- coding: utf-8 -*-
"""관리대장 변경 이력(`ledger_history`) 보존 정리 도구 (감사 4회차 4.1-2).

`ledger_history`는 웹 등록·수정·삭제와 엑셀 동기화마다 한 줄씩 쌓이고, 00번 재빌드 때마다 새 DB로
전량 옮겨진다. 지우는 경로가 없어 끝없이 커진다. 이 도구는 오래된 이력을 **JSONL 파일로 보관한 뒤**
DB에서 뺀다.

    python tools/archive_ledger_history.py                 # 점검만 (아무것도 바꾸지 않음)
    python tools/archive_ledger_history.py --days 730      # 보존 기간 지정(기본 365일)
    python tools/archive_ledger_history.py --apply         # 보관 파일 작성·검증 → DB에서 삭제

남기는 이력(보존 기간과 무관):
- 대장 항목마다 가장 최근 `INSERT` 한 줄과 가장 최근 엑셀 반영 이력(`EXCEL_APPLIED`·`EXCEL_SYNC_INSERT`·
  `EXCEL_SYNC_UPDATE`) 한 줄. 엑셀→DB 삭제 판정(`_web_only_ledger_ids`)이 "웹에서 등록했지만 엑셀에 한
  번도 쓰이지 않은 항목"을 이 두 종류로 가려낸다. 지우면 엑셀에서 지운 행이 DB에 반영되지 않거나,
  반대로 아직 엑셀에 없는 웹 등록이 삭제될 수 있다.
- 항목마다 가장 최근 `DELETE` 한 줄. 웹 복원(`restore_ledger_item`)이 삭제 직전 스냅숏을
  읽기 때문이다. 없으면 오래된 삭제는 "엑셀에서 직접 고쳐 주세요"로 거절된다.
  (2026-09-28 감사 Gap-2)
- `changed_at`을 해석할 수 없는 이력.

실행 조건(--apply): 02번 웹관리서버와 00번 파이프라인이 꺼져 있어야 한다.
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pipeline_guard import PipelineGuard, pid_is_alive  # noqa: E402
from write_guard import ensure_writable  # noqa: E402

EXCEL_SEEN_ACTIONS = ("EXCEL_APPLIED", "EXCEL_SYNC_INSERT", "EXCEL_SYNC_UPDATE")
DELETE_CHUNK = 500


def parse_changed_at(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text[:19] if "H" in fmt else text[:10], fmt)
        except ValueError:
            continue
    return None


def plan_history_archive(rows, cutoff: datetime.datetime):
    """(보관·삭제할 history_id 목록, 남길 id 집합). rows: history_id·ledger_id·action·changed_at."""
    keep = set()
    latest = {}
    for r in rows:
        if r["action"] == "INSERT":
            group = "insert"
        elif r["action"] == "DELETE":
            # 웹 복원(restore_ledger_item)이 삭제 직전 스냅숏을 읽으므로 항목별 최신
            # 1건은 기간과 관계없이 남긴다. 없으면 오래된 삭제의 복원이 거절된다.
            # (2026-09-28 감사 Gap-2)
            group = "delete"
        else:
            group = "excel" if r["action"] in EXCEL_SEEN_ACTIONS else None
        if group:
            key = (r["ledger_id"], group)
            if key not in latest or r["history_id"] > latest[key]:
                latest[key] = r["history_id"]
    keep.update(latest.values())
    archive = []
    for r in rows:
        hid = r["history_id"]
        if hid in keep:
            continue
        when = parse_changed_at(r["changed_at"])
        if when is None or when >= cutoff:
            keep.add(hid)
            continue
        archive.append(hid)
    return archive, keep


def preconditions(system_dir: Path) -> list:
    problems = []
    if PipelineGuard(system_dir).is_locked():
        problems.append("파이프라인 락(.pipeline.lock)이 있습니다. 00번 파이프라인이 끝난 뒤 실행하세요.")
    web_lock = system_dir / ".web_server.lock"
    if web_lock.exists():
        try:
            pid = int(json.loads(web_lock.read_text(encoding="utf-8")).get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pid = 0
        if pid and pid != os.getpid() and pid_is_alive(pid):
            problems.append("02번 웹관리서버가 실행 중입니다. 서버 창을 닫은 뒤 실행하세요.")
    return problems


def archive_history(db_path: Path, backup_root: Path, days: int = 365, apply: bool = False,
                    now: Optional[datetime.datetime] = None) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        return {"success": False, "error": f"DB가 없습니다: {db_path}"}
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=days)

    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT history_id, ledger_id, action, changed_at, snapshot FROM ledger_history ORDER BY history_id"
        )]
        archive_ids, keep = plan_history_archive(rows, cutoff)
        result = {"success": True, "total": len(rows), "archive": len(archive_ids), "keep": len(keep),
                  "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"), "applied": False}
        if not apply or not archive_ids:
            return result

        ensure_writable(db_path, "SQLite DB")
        stamp = now.strftime("%Y%m%d_%H%M%S")
        dest = Path(backup_root) / f"{stamp}_ledger_history_archive"
        ensure_writable(dest, "이력 보관 폴더")
        dest.mkdir(parents=True, exist_ok=False)
        archive_file = dest / "ledger_history.jsonl"
        wanted = set(archive_ids)
        with open(archive_file, "w", encoding="utf-8", newline="\n") as fp:
            for r in rows:
                if r["history_id"] in wanted:
                    fp.write(json.dumps(r, ensure_ascii=False) + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        # 보관 파일을 다시 읽어 건수와 ID가 일치할 때만 DB에서 지운다.
        with open(archive_file, "r", encoding="utf-8") as fp:
            written = {json.loads(line)["history_id"] for line in fp if line.strip()}
        if written != wanted:
            raise RuntimeError("보관 파일 검증 실패: 기록한 이력과 삭제 대상이 다릅니다. DB는 바꾸지 않았습니다.")
        (dest / "MANIFEST.json").write_text(json.dumps({
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"), "db": str(db_path), "days": days,
            "cutoff": result["cutoff"], "archived": len(archive_ids),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        conn.execute("BEGIN IMMEDIATE")
        try:
            for start in range(0, len(archive_ids), DELETE_CHUNK):
                chunk = archive_ids[start:start + DELETE_CHUNK]
                conn.execute(f"DELETE FROM ledger_history WHERE history_id IN ({','.join('?' * len(chunk))})", chunk)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        result.update({"applied": True, "archive_file": str(archive_file)})
        return result
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="관리대장 변경 이력 보존 정리")
    parser.add_argument("--days", type=int, default=365, help="보존 기간(일). 이보다 오래된 이력을 보관 대상으로 본다.")
    parser.add_argument("--apply", action="store_true", help="보관 파일을 쓰고 DB에서 삭제한다.")
    parser.add_argument("--db", default=str(SYSTEM_DIR / "data_requests.db"))
    args = parser.parse_args(argv)
    if args.days < 30:
        print("✗ 보존 기간은 30일 이상이어야 합니다.")
        return 2
    if args.apply:
        problems = preconditions(SYSTEM_DIR)
        if problems:
            print("✗ 실행 조건이 맞지 않습니다:")
            for p in problems:
                print(f"   - {p}")
            return 1
    res = archive_history(Path(args.db), SYSTEM_DIR / ".maintenance_backup", days=args.days, apply=args.apply)
    if not res.get("success"):
        print(f"✗ {res.get('error')}")
        return 1
    print(f"전체 이력 {res['total']}건 / 보관 대상 {res['archive']}건 / 유지 {res['keep']}건 (기준: {res['cutoff']} 이전)")
    if res["applied"]:
        print(f"✓ 보관 후 삭제 완료: {res['archive_file']}")
    elif args.apply:
        print("보관할 이력이 없습니다.")
    else:
        print("점검만 했습니다. 실제로 정리하려면 --apply를 붙이세요.")
    return 0


if __name__ == "__main__":
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    sys.exit(main())
