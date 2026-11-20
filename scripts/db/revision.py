"""Content identity survives both file replacement and SQLite online backup."""

import uuid

CONTENT_TABLES = ("documents", "qa_items", "request_ledger", "ledger_history")


def ensure_revision(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dataset_revision (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        build_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute(
        "INSERT OR IGNORE INTO dataset_revision VALUES (1, ?, 0)", (uuid.uuid4().hex,)
    )
    for table in CONTENT_TABLES:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS revision_{table}_{action.lower()}
                AFTER {action} ON {table} BEGIN
                    UPDATE dataset_revision SET revision = revision + 1 WHERE singleton = 1;
                END""")
