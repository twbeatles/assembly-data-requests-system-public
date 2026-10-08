# -*- coding: utf-8 -*-
"""파이프라인 중 웹 변경 병합(SRP: 스왑 전 동시성 병합)."""
import sqlite3
from pathlib import Path


def merge_concurrent_ledger_updates(ledger_records, db_path, started_at: str):
    """load_request_ledger 이후 웹에서 들어온 대장 행을 스왑 전에 합친다."""
    db_path = Path(db_path)
    if not db_path.exists():
        return ledger_records
    by_id = {r.get("ledger_id"): r for r in ledger_records if r.get("ledger_id")}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_ledger'"
        ).fetchone()
        if not exists:
            return ledger_records
        rows = conn.execute("SELECT * FROM request_ledger").fetchall()
        added = 0
        for row in rows:
            item = dict(row)
            lid = item.get("ledger_id")
            if not lid:
                continue
            ua = str(item.get("updated_at") or "")
            if lid not in by_id or (started_at and ua >= started_at):
                by_id[lid] = item
                added += 1
        if added:
            print(f"✓ 파이프라인 중 웹 변경 {added}건을 스왑 전에 병합")
        return list(by_id.values())
    except Exception:
        return ledger_records
    finally:
        if conn:
            conn.close()
