"""Ledger mutations and their Excel delivery intent share one SQLite commit."""
import json
from contextlib import closing
import sqlite3
import time
import uuid
from pathlib import Path


def ensure_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS ledger_outbox (operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ledger_receipts (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, ledger_id TEXT NOT NULL)")


def enqueue(conn, item, action):
    operation_id = uuid.uuid4().hex
    item["_operation_id"] = operation_id
    op = dict(operation_id=operation_id, item=dict(item), action=action,
              timestamp=time.time(), attempts=0)
    conn.execute("INSERT INTO ledger_outbox VALUES (?, ?)",
                 (operation_id, json.dumps(op, ensure_ascii=False)))
    return item


def read_operations(db_path):
    if not Path(db_path).exists():
        return []
    from extractors.ledger.workbook import connect_readonly
    with closing(connect_readonly(db_path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ledger_outbox'").fetchone():
            return []
        return [json.loads(row[0]) for row in conn.execute("SELECT payload FROM ledger_outbox ORDER BY rowid")]


def acknowledge(db_path, ids):
    if not ids:
        return
    from write_guard import ensure_writable
    ensure_writable(db_path, "엑셀 작업 인계")
    with closing(sqlite3.connect(str(db_path), timeout=5)) as conn, conn:
        conn.executemany("DELETE FROM ledger_outbox WHERE operation_id=?", [(i,) for i in ids])


def copy_delivery_state(source, dest):
    if not Path(source).exists():
        return
    from extractors.ledger.workbook import connect_readonly
    with closing(connect_readonly(source)) as conn:
        for table in ("ledger_outbox", "ledger_receipts"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                for row in conn.execute(f"SELECT * FROM {table}"):
                    dest.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", tuple(row))
    dest.commit()
