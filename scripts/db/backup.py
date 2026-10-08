# -*- coding: utf-8 -*-
"""SQLite 스냅숏 백업 (제안서 D5).

`.ledger_backup/`은 **마스터 엑셀** 백업이다(불변조건 33). `data_requests.db` 자체의
스냅숏 정책은 없었다.

DB는 대부분 엑셀과 파싱 마크다운에서 다시 만들 수 있지만, **`ledger_history`는 DB에만
있다.** 재빌드 때 `copy_ledger_history()`가 옮기고는 있으나 파일이 깨지면 그대로 사라진다.

`VACUUM INTO`를 쓴다. 빈 페이지를 뺀 상태로 떠지고, WAL 중에도 일관된 스냅숏이 된다
(파일 복사는 -wal이 남아 있으면 깨진 사본을 만든다).
"""

from typing import Any
import datetime
import sqlite3
from pathlib import Path

BACKUP_DIR_NAME = ".db_backup"
DAILY_SUBDIR = "daily"
DEFAULT_KEEP_DAILY = 7
DEFAULT_KEEP_WEEKLY = 4
HISTORY_SUBDIR = "history"
DEFAULT_KEEP_HISTORY = 30


def backup_dir(base_dir) -> Path:
    return Path(base_dir) / BACKUP_DIR_NAME


def snapshot_database(db_path, base_dir=None, keep_daily: int = DEFAULT_KEEP_DAILY) -> dict:
    """하루 한 본 스냅숏을 뜨고 오래된 것을 지운다. 같은 날 다시 부르면 덮어쓴다."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"success": False, "error": "DB 파일이 없습니다", "path": str(db_path)}
    base = Path(base_dir) if base_dir else db_path.parent
    out_dir = backup_dir(base) / DAILY_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    target = out_dir / f"{db_path.stem}_{stamp}.db"

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        # VACUUM INTO는 대상 파일이 이미 있으면 실패한다. 같은 날 두 번째 호출을 위해 지운다.
        if target.exists():
            target.unlink()
        conn.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as e:
        return {"success": False, "error": f"스냅숏 실패: {e}", "path": str(target)}
    finally:
        if conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    removed = _prune(out_dir, f"{db_path.stem}_*.db", keep_daily)
    return {
        "success": True,
        "path": str(target),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "removed": removed,
    }


def snapshot_ledger_history(db_path, base_dir=None, keep: int = DEFAULT_KEEP_HISTORY) -> dict:
    """대장 이력만 가볍게 덤프한다.

    전체 스냅숏은 수백 MB라 매일 여러 본을 두기 어렵다. 그런데 정작 **다시 만들 수 없는
    것은 이력뿐**이므로, 이것만 따로 오래 남긴다.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return {"success": False, "error": "DB 파일이 없습니다"}
    base = Path(base_dir) if base_dir else db_path.parent
    out_dir = backup_dir(base) / HISTORY_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().strftime("%Y%m%d")
    target = out_dir / f"ledger_history_{stamp}.jsonl"

    import json
    conn = None
    rows = 0
    try:
        from extractors.ledger.workbook import connect_readonly
        conn = connect_readonly(db_path)
        conn.row_factory = sqlite3.Row
        with open(target, "w", encoding="utf-8") as fp:
            for r in conn.execute("SELECT * FROM ledger_history ORDER BY history_id"):
                fp.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
                rows += 1
    except (sqlite3.Error, OSError) as e:
        return {"success": False, "error": f"이력 덤프 실패: {e}"}
    finally:
        if conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    removed = _prune(out_dir, "ledger_history_*.jsonl", keep)
    return {"success": True, "path": str(target), "rows": rows, "removed": removed}


def _prune(directory: Path, pattern: str, keep: int) -> list:
    """이름순(= 날짜순) 최신 `keep`개만 남긴다."""
    if keep <= 0:
        return []
    files = sorted(directory.glob(pattern))
    removed = []
    for old in files[:-keep]:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            pass
    return removed


def run_all(db_path, base_dir=None) -> dict:
    """파이프라인이 끝난 뒤 부르는 진입점. 실패해도 파이프라인을 세우지 않는다."""
    out: dict[str, Any] = {"database": None, "history": None}
    try:
        out["database"] = snapshot_database(db_path, base_dir)
    except Exception as e:
        out["database"] = {"success": False, "error": str(e)}
    try:
        out["history"] = snapshot_ledger_history(db_path, base_dir)
    except Exception as e:
        out["history"] = {"success": False, "error": str(e)}
    return out
