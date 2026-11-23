# -*- coding: utf-8 -*-
"""검색 관측 로그(`search_log`) 전용 SQLite 저장소.

예전에는 `GET /api/search`가 매번 운영 DB(`data_requests.db`)에
`INSERT INTO search_log` + `commit`을 했다. 조회만으로 WAL이 차고, 동시 검색이
쓰기 경합(`busy_timeout` 초과 후 조용한 유실)을 일으키며, 파이프라인
`atomic_swap` 순간에 쓴 행이 교체와 함께 버려졌다. 읽기 경로는 정말 읽기
전용이어야 한다. (PROJECT_AUDIT.md ISSUE-001)

그래서 관측 로그는 본체 DB와 다른 파일(`<db_stem>_searchlog.db`)에 둔다.
검색 결과 정확도와 무관한 부가 기록이라, 쓰기 실패는 본체 트랜잭션에
영향을 주지 않고 유실 카운터로만 남긴다.
"""

import datetime
import sqlite3
import threading
from pathlib import Path

from write_guard import ensure_writable

SEARCH_LOG_DDL = """
    CREATE TABLE IF NOT EXISTS search_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        mode TEXT,
        result_count INTEGER,
        elapsed_ms INTEGER,
        used_fts INTEGER,
        searched_at TEXT
    )
"""

SEARCH_LOG_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_search_log_at ON search_log(searched_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_search_log_zero ON search_log(result_count, searched_at DESC)",
)

LEGACY_IMPORT_LIMIT = 5000

_DROPPED = 0
_DROPPED_LOCK = threading.Lock()
_IMPORTED = set()
_IMPORTED_LOCK = threading.Lock()


def dropped_count() -> int:
    """쓰기에 실패해 버린 로그 행 누적 수. `/api/sync` 상태로 노출한다."""
    with _DROPPED_LOCK:
        return _DROPPED


def _note_dropped() -> None:
    global _DROPPED
    with _DROPPED_LOCK:
        _DROPPED += 1


def log_db_path_for(db_path) -> Path:
    """본체 DB 경로에서 로그 DB 경로를 유도한다. 호출자가 명시 경로를 주면 그것을 쓴다."""
    p = Path(db_path)
    return p.with_name(f"{p.stem}_searchlog{p.suffix or '.db'}")


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def ensure_log_db(path, legacy_db_path=None) -> None:
    """로그 DB 스키마를 준비하고, 구 본체 DB의 기존 행을 한 번만 가져온다."""
    path = Path(path)
    ensure_writable(path, "검색 로그 DB")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute(SEARCH_LOG_DDL)
        for stmt in SEARCH_LOG_INDEXES:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    _import_legacy_once(path, legacy_db_path)


def _import_legacy_once(path, legacy_db_path) -> None:
    """본체 DB에 남아 있던 기존 관측 행을 새 로그 DB로 옮긴다. 딱 한 번, 최선을 다해서만."""
    key = str(Path(path).resolve())
    with _IMPORTED_LOCK:
        if key in _IMPORTED:
            return
        _IMPORTED.add(key)
    try:
        if legacy_db_path is None:
            return
        legacy = Path(legacy_db_path)
        if not legacy.exists() or legacy.resolve() == Path(path).resolve():
            return
        src = connect(legacy)
        try:
            try:
                rows = src.execute(
                    "SELECT query, mode, result_count, elapsed_ms, used_fts, searched_at "
                    "FROM search_log ORDER BY searched_at DESC LIMIT ?",
                    (LEGACY_IMPORT_LIMIT,),
                ).fetchall()
            except sqlite3.Error:
                return
        finally:
            src.close()
        if not rows:
            return
        dst = connect(path)
        try:
            if dst.execute("SELECT COUNT(*) FROM search_log").fetchone()[0] > 0:
                return
            dst.executemany(
                "INSERT INTO search_log (query, mode, result_count, elapsed_ms, "
                "used_fts, searched_at) VALUES (?, ?, ?, ?, ?, ?)",
                [tuple(r) for r in rows],
            )
            dst.commit()
        finally:
            dst.close()
    except Exception:
        pass


def append(
    path, *, query, mode, result_count, elapsed_ms, used_fts, legacy_db_path=None
) -> bool:
    """관측 행 1건을 기록한다. 실패해도 예외를 내지 않고 False를 돌려준다."""
    try:
        ensure_log_db(path, legacy_db_path)
        conn = connect(path)
        try:
            conn.execute(
                "INSERT INTO search_log (query, mode, result_count, elapsed_ms, "
                "used_fts, searched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(query)[:300],
                    mode,
                    int(result_count),
                    int(elapsed_ms),
                    1 if used_fts else 0,
                    datetime.datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        _note_dropped()
        return False


def read_insights(path, limit=20, days=90) -> dict:
    """검색 로그 요약. 파일이 없거나 깨졌으면 빈 요약을 돌려준다(읽기 경로 무부수)."""
    empty = {"success": True, "zero_result": [], "popular": [], "slow": []}
    try:
        if not Path(path).exists():
            return empty
        limit = max(1, min(int(limit), 200))
        days = max(1, min(int(days), 3650))
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        conn = connect(path)
        try:
            cur = conn.cursor()
            zero = [
                dict(r)
                for r in cur.execute(
                    "SELECT query, COUNT(*) AS hits, MAX(searched_at) AS last_at FROM search_log "
                    "WHERE result_count = 0 AND searched_at >= ? "
                    "GROUP BY query ORDER BY hits DESC, last_at DESC LIMIT ?",
                    (since, limit),
                )
            ]
            popular = [
                dict(r)
                for r in cur.execute(
                    "SELECT query, COUNT(*) AS hits FROM search_log WHERE searched_at >= ? "
                    "GROUP BY query ORDER BY hits DESC LIMIT ?",
                    (since, limit),
                )
            ]
            slow = [
                dict(r)
                for r in cur.execute(
                    "SELECT query, MAX(elapsed_ms) AS worst_ms, AVG(elapsed_ms) AS avg_ms, "
                    "SUM(used_fts) AS fts_hits, COUNT(*) AS hits FROM search_log "
                    "WHERE searched_at >= ? GROUP BY query ORDER BY worst_ms DESC LIMIT ?",
                    (since, limit),
                )
            ]
        finally:
            conn.close()
        return {"success": True, "zero_result": zero, "popular": popular, "slow": slow}
    except Exception:
        return empty


def prune(path, retention_days=90, keep_zero_result=True) -> int:
    """오래된 관측 행을 rolling 삭제한다. 본체 DB의 prune 규칙을 그대로 쓴다."""
    from db.database_manager import DatabaseManager

    path = Path(path)
    if not path.exists():
        return 0
    ensure_log_db(path)
    conn = connect(path)
    try:
        return DatabaseManager.prune_search_log(
            conn, retention_days=retention_days, keep_zero_result=keep_zero_result
        )
    finally:
        conn.close()
