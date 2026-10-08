# -*- coding: utf-8 -*-
"""
Database Manager Module (SOLID: SRP & DIP).
Handles SQLite database connection lifecycle, high-performance PRAGMA configurations,
DDL schema creation, Trigram FTS5 full-text indexing, triggers, composite indexes,
and safe atomic shadow-database swap.
"""

import os
import sys
import sqlite3
import shutil
import time
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from . import fts_spec
from .migrations import (
    SCHEMA_VERSION,
    SchemaTooNewError,
    apply_migrations,
    get_version,
    rebuild_fts,
    set_version,
)

def _readonly_uri(db_path, immutable: bool = False) -> str:
    """읽기 전용 URI. 경로에 공백·쉼표·괄호·한글이 있어도 되도록 퍼센트 인코딩한다.

    이 저장소의 실제 경로가 바로 그런 모양이라, 인코딩하지 않으면 연결이 실패한다.
    `immutable=1`은 `-wal`/`-shm` 사이드카까지 건드리지 않는다(감사 R4-01).
    """
    from urllib.parse import quote
    uri = "file:" + quote(Path(db_path).resolve().as_posix(), safe="/:") + "?mode=ro"
    if immutable:
        uri += "&immutable=1"
    return uri


class DatabaseManager:
    """Manages SQLite connections, schemas, FTS5 triggers, and atomic migrations."""

    FTS5_AVAILABLE: bool = True
    SCHEMA_VERSION: int = SCHEMA_VERSION

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @staticmethod
    def get_connection(
        db_path: Path,
        row_factory: bool = True,
        busy_timeout: int = 5000,
        cache_size: int = -32000,
        readonly: bool = False
    ) -> sqlite3.Connection:
        """Factory method returning an optimized SQLite connection.

        `readonly=True`면 URI 모드로 열고 `query_only`를 건다. 검색 경로가 실수로 쓰는 일을
        구조적으로 막는다(제안서 D9). WAL 읽기는 읽기 전용 연결에서도 정상 동작한다.
        """
        if readonly:
            conn = sqlite3.connect(_readonly_uri(db_path), uri=True, check_same_thread=False)
        else:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
        if row_factory:
            conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout};")
        if not readonly:
            conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute(f"PRAGMA cache_size = {cache_size};")
        conn.execute("PRAGMA foreign_keys = ON;")
        if readonly:
            conn.execute("PRAGMA query_only = ON;")
        return conn

    def get_conn(self, row_factory: bool = True, readonly: bool = False) -> sqlite3.Connection:
        """Returns connection to self.db_path."""
        return self.get_connection(self.db_path, row_factory=row_factory, readonly=readonly)

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection):
        """Initializes tables, FTS5 virtual tables, triggers, and indexes IF NOT EXISTS (non-destructive)."""
        if get_version(conn) > SCHEMA_VERSION:
            raise SchemaTooNewError("지원하지 않는 최신 DB 버전입니다.")
        from .ledger_outbox import ensure_tables
        ensure_tables(conn)
        cur = conn.cursor()
        # 테이블을 만들기 **전에** 새 DB인지 본다. 만든 뒤에 보면 새 DB도 '기존 DB'가 되어
        # 빌드할 때마다 빈 테이블에 마이그레이션이 돌고 로그가 지저분해진다.
        was_fresh = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone() is None
        cur.execute("PRAGMA page_size = 4096;")
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA synchronous = NORMAL;")
        cur.execute("PRAGMA cache_size = -64000;")
        cur.execute("PRAGMA temp_store = MEMORY;")

        # 1. Documents Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                year TEXT,
                request_date TEXT,
                institution TEXT,
                requester TEXT,
                doc_number TEXT,
                version TEXT,
                title TEXT,
                department TEXT,
                contact_person TEXT,
                question_list TEXT,
                answer_summary TEXT,
                answer_full_text TEXT,
                has_tables TEXT,
                table_count INTEGER,
                table_summary TEXT,
                topic_tags TEXT,
                parsed_md_path TEXT,
                original_path TEXT,
                full_markdown TEXT,
                linked_ledger_id TEXT
            )
        """)

        # 2. QA Items Table (1:N)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS qa_items (
                qa_id TEXT PRIMARY KEY,
                doc_id TEXT,
                year TEXT,
                request_date TEXT,
                institution TEXT,
                requester TEXT,
                doc_number TEXT,
                q_num INTEGER,
                question_title TEXT,
                answer_full TEXT,
                answer_markdown TEXT,
                has_tables TEXT,
                parsed_md_path TEXT,
                original_path TEXT,
                FOREIGN KEY (doc_id) REFERENCES documents (doc_id)
            )
        """)

        # 3. Request Ledger Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS request_ledger (
                ledger_id TEXT PRIMARY KEY,
                year TEXT,
                seq_no TEXT,
                party TEXT,
                requester TEXT,
                aide TEXT,
                title TEXT,
                details TEXT,
                request_date TEXT,
                deadline TEXT,
                submit_date TEXT,
                department TEXT,
                status TEXT,
                note TEXT,
                request_type TEXT,
                linked_doc_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # 4. Request Ledger History (Audit Trail)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ledger_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_id TEXT,
                action TEXT,
                changed_at TEXT,
                snapshot TEXT,
                -- 어느 칸이 바뀌었는지·누가 바꿨는지 (제안서 D7). 스냅숏 JSON 한 덩어리만으로는
                -- "이 항목의 마감일을 누가 언제 바꿨나"를 SQL로 물어볼 수 없었다.
                -- **기본 스키마에 있어야 한다.** 마이그레이션(v2)에만 두면 새로 만든 DB에는
                -- 컬럼이 생기지 않는다(마이그레이션은 기존 DB를 올릴 때만 돈다).
                changed_fields TEXT,
                actor TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_history_ledger_id ON ledger_history(ledger_id)")

        # 5. Search Log (제안서 S9) — 어떤 질의가 0건으로 끝났는지 알아야 사전을 고칠 수 있다.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                mode TEXT,
                result_count INTEGER,
                elapsed_ms INTEGER,
                used_fts INTEGER,
                searched_at TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_search_log_at ON search_log(searched_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_search_log_zero ON search_log(result_count, searched_at DESC)")

        # FTS5 External Content Virtual Tables & Triggers (정의는 fts_spec 한 곳)
        try:
            for fts_name in fts_spec.fts_table_names():
                cur.execute(fts_spec.create_sql(fts_name, if_not_exists=True))
                for trg in fts_spec.trigger_sqls(fts_name, if_not_exists=True):
                    cur.execute(trg)
        except Exception as e:
            DatabaseManager.FTS5_AVAILABLE = False
            print(f"ℹ️ [DB 알림] FTS5 Trigram 가상 테이블 생성 생략 또는 미지원: {e}")

        DatabaseManager._create_indexes(cur)

        conn.commit()

        # 스키마 버전 확인과 마이그레이션. 구 스키마 DB를 조용히 쓰지 않는다(제안서 D1).
        apply_migrations(conn, fresh_hint=was_fresh)

    @staticmethod
    def _create_indexes(cur):
        """복합·커버링 인덱스. 예전에는 같은 목록이 세 곳에 복사돼 있었다."""
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_year_date ON documents(year, request_date DESC, doc_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_date_order ON documents(request_date DESC, doc_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_year ON documents(year)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_requester ON documents(requester)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_institution ON documents(institution)")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_year_date ON qa_items(year, request_date DESC, qa_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_date_order ON qa_items(request_date DESC, qa_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_doc_id ON qa_items(doc_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_requester ON qa_items(requester)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qa_doc_qnum ON qa_items(doc_id, q_num)")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_year_date ON request_ledger(year, request_date DESC, ledger_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date_order ON request_ledger(request_date DESC, ledger_id ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_requester ON request_ledger(requester)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_status ON request_ledger(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_linked ON request_ledger(linked_doc_id)")
        # 마감 임박·경과 조회가 전 행을 훑던 자리 (제안서 D4)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_deadline ON request_ledger(deadline)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ledger_submit_date ON request_ledger(submit_date)")

    @staticmethod
    def init_schema(conn: sqlite3.Connection, drop_existing: bool = False):
        """Initializes tables, optionally dropping old triggers/tables for a clean build."""
        if drop_existing:
            cur = conn.cursor()
            # 트리거·FTS 가상 테이블은 fts_spec이 정의를 갖고 있다. 이름을 여기 다시 적으면
            # 인덱스를 하나 추가할 때 이 목록만 빠져 유령 트리거가 남는다.
            for stmt in fts_spec.drop_sqls():
                cur.execute(stmt)
            cur.execute("DROP TABLE IF EXISTS ledger_history")
            cur.execute("DROP TABLE IF EXISTS request_ledger")
            cur.execute("DROP TABLE IF EXISTS qa_items")
            cur.execute("DROP TABLE IF EXISTS documents")
            # 새 DB로 취급되도록 버전을 되돌린다. ensure_schema가 현재 버전을 다시 찍는다.
            set_version(conn, 0)
            conn.commit()

        DatabaseManager.ensure_schema(conn)

    @staticmethod
    def insert_records(
        conn: sqlite3.Connection,
        records: List[Dict[str, Any]],
        all_qa_items: List[Dict[str, Any]],
        ledger_records: List[Dict[str, Any]]
    ):
        """Bulk-inserts documents, qa_items, and request_ledger."""
        cur = conn.cursor()

        doc_tuples = [
            (
                r["doc_id"], r["year"], r["request_date"], r["institution"], r["requester"], r["doc_number"],
                r["version"], r["title"], r["department"], r["contact_person"], r["question_list"],
                r["answer_summary"], r["answer_full_text"], r["has_tables"], r["table_count"], r["table_summary"],
                r["topic_tags"], r["parsed_md_path"], r["original_path"], r["full_markdown"],
                r.get("linked_ledger_id", "")
            )
            for r in records
        ]
        cur.executemany("INSERT OR REPLACE INTO documents VALUES (" + ",".join(["?"] * 21) + ")", doc_tuples)

        qa_tuples = [
            (
                q["qa_id"], q["doc_id"], q["year"], q["request_date"], q["institution"], q["requester"],
                q["doc_number"], q["q_num"], q["question_title"], q["answer_full"], q["answer_markdown"],
                q["has_tables"], q["parsed_md_path"], q["original_path"]
            )
            for q in all_qa_items
        ]
        cur.executemany("INSERT OR REPLACE INTO qa_items VALUES (" + ",".join(["?"] * 14) + ")", qa_tuples)

        ledger_tuples = [
            (
                lr["ledger_id"], lr["year"], lr["seq_no"], lr["party"], lr["requester"], lr["aide"],
                lr["title"], lr["details"], lr["request_date"], lr["deadline"], lr["submit_date"],
                lr["department"], lr["status"], lr["note"], lr["request_type"], lr["linked_doc_id"],
                lr["created_at"], lr["updated_at"]
            )
            for lr in ledger_records
        ]
        cur.executemany("INSERT OR REPLACE INTO request_ledger VALUES (" + ",".join(["?"] * 18) + ")", ledger_tuples)
        conn.commit()

    @staticmethod
    def copy_ledger_history(source_db_path: Path, dest_conn: sqlite3.Connection) -> int:
        """기존 운영 DB의 ledger_history를 새 DB로 이관한다. 원본이 없으면 0."""
        source_db_path = Path(source_db_path)
        if not source_db_path.exists():
            return 0
        src = None
        try:
            src = sqlite3.connect(str(source_db_path))
            exists = src.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_history'"
            ).fetchone()
            if not exists:
                return 0
            src_cols = {row[1] for row in src.execute("PRAGMA table_info(ledger_history)").fetchall()}
            if {"changed_fields", "actor"} <= src_cols:
                cols = "ledger_id, action, changed_at, snapshot, changed_fields, actor"
            else:
                # 아직 마이그레이션 전인 구 DB(두 컬럼 없음)는 4컬럼으로 폴백한다.
                cols = "ledger_id, action, changed_at, snapshot"
            rows = src.execute(f"SELECT {cols} FROM ledger_history").fetchall()
            if not rows:
                return 0
            placeholders = ",".join(["?"] * len(cols.split(",")))
            dest_conn.executemany(
                f"INSERT INTO ledger_history ({cols}) VALUES ({placeholders})",
                rows
            )
            dest_conn.commit()
            return len(rows)
        except Exception as e:
            # 호출자의 int 규약은 유지하되 실패를 조용히 0건과 구분 불가하게 두지 않는다.
            print(f"[이력 이관 경고] ledger_history 이관 실패(0건으로 계속): {e}")
            return 0
        finally:
            if src:
                try:
                    src.close()
                except Exception:
                    pass

    @staticmethod
    def prune_search_log(conn: sqlite3.Connection, retention_days: int = 90,
                         keep_zero_result: bool = True) -> int:
        """오래된 검색 로그를 rolling 삭제한다. (감사 R6-04)

        `search_log`는 검색할 때마다 1행씩 쌓이고 지우는 코드가 없어 영원히
        늘어난다. `searched_at`(ISO `YYYY-MM-DDTHH:MM:SS`) 기준 보존일 초과분을
        지운다. 0건 질의는 동의어 사전 개선(S9)의 재료라 기본 보존한다.
        반환: 삭제한 행 수.
        """
        retention_days = int(retention_days)
        if retention_days < 0:
            raise ValueError(f"retention_days는 0 이상이어야 합니다: {retention_days}")
        cutoff = (datetime.datetime.now()
                  - datetime.timedelta(days=retention_days)).isoformat(timespec="seconds")
        sql = "DELETE FROM search_log WHERE searched_at IS NOT NULL AND searched_at < ?"
        if keep_zero_result:
            sql += " AND COALESCE(result_count, 0) != 0"
        cur = conn.execute(sql, (cutoff,))
        conn.commit()
        return cur.rowcount or 0


    @staticmethod
    def build_fts_and_indexes(conn: sqlite3.Connection):
        """FTS5 가상 테이블·동기화 트리거·복합 인덱스를 새로 만든다(재빌드 경로).

        정의는 `fts_spec` 한 곳에서 온다. 예전에는 같은 DDL이 `ensure_schema`와 여기에
        두 벌로 적혀 있어, 한쪽만 고치면 신규 설치와 재빌드의 인덱스 구성이 갈라졌다.
        """
        cur = conn.cursor()
        fts_ok = rebuild_fts(conn)
        if fts_ok:
            for fts_name in fts_spec.fts_table_names():
                print(f"SQLite FTS5 전문 검색 인덱스(trigram) 구축 완료: {fts_name}")
        else:
            DatabaseManager.FTS5_AVAILABLE = False

        DatabaseManager._create_indexes(cur)
        set_version(conn, SCHEMA_VERSION)
        conn.commit()
        DatabaseManager.optimize_for_search(conn, fts=fts_ok, vacuum=True)

    @staticmethod
    def optimize_for_search(conn: sqlite3.Connection, fts: bool = True, vacuum: bool = False) -> list:
        """검색용 DB를 정리한다. 실패해도 검색 결과는 같다(속도·크기만 영향). 반환: 수행한 단계.

        - FTS5 `optimize`: 한 번에 채운 인덱스도 세그먼트 b-tree가 여러 개로 남는다. 하나로
          합치면 MATCH가 세그먼트마다 반복 조회하지 않아 문서가 많을수록 빨라지고 파일도 준다.
        - `ANALYZE`: 쿼리 플래너 통계. 연도·마감 인덱스를 전 행 스캔 대신 쓰게 한다.
          (`PRAGMA optimize`는 통계가 없는 새 DB에서는 거의 아무것도 하지 않는다.)
        - `VACUUM`: 재빌드 직후 한 번만. 빈 페이지를 없애 파일 크기를 줄인다.
        """
        done = []
        steps = []
        if fts:
            for name in fts_spec.fts_table_names():
                steps.append((f"FTS optimize: {name}", f"INSERT INTO {name}({name}) VALUES('optimize')"))
        steps.append(("ANALYZE", "ANALYZE"))
        steps.append(("PRAGMA optimize", "PRAGMA optimize"))
        for label, sql in steps:
            try:
                conn.execute(sql)
                conn.commit()
                done.append(label)
            except sqlite3.Error as e:
                print(f"DB 최적화 알림({label}): {e}")
        if vacuum:
            for label, sql in (("VACUUM", "VACUUM"), ("WAL checkpoint", "PRAGMA wal_checkpoint(TRUNCATE)")):
                try:
                    conn.execute(sql)
                    done.append(label)
                except sqlite3.Error as e:
                    print(f"DB 최적화/정리 알림({label}): {e}")
        return done

    @staticmethod
    def _target_writable(target_db_path: Path) -> bool:
        """교체 전 대상 DB 쓰기 가능 여부를 짧게 확인한다. (ISSUE-002)

        backup()은 잠긴 대상에서 무한 대기할 수 있어(재현됨),
        여기서 짧게 실패시켜 재시도 루프로 돌려보낸다.
        BEGIN IMMEDIATE만 걸고 ROLLBACK하므로 실제 쓰기는 일어나지 않는다.
        """
        probe = None
        try:
            probe = sqlite3.connect(str(target_db_path), timeout=0.5)
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("ROLLBACK")
            return True
        except sqlite3.Error:
            return False
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

    @staticmethod
    def atomic_swap(
        tmp_db_path: Path,
        target_db_path: Path,
        max_retries: int = 5,
        retry_delay: float = 0.5
    ):
        """Replaces target production DB with tmp DB atomically, cleaning up WAL/SHM files,
        with Windows file-lock retry handling to avoid PermissionError during server runtime."""
        tmp_db_path = Path(tmp_db_path)
        target_db_path = Path(target_db_path)

        if not tmp_db_path.exists():
            raise FileNotFoundError(f"임시 DB 파일을 찾을 수 없습니다: {tmp_db_path}")

        # 1. target_db_path가 존재하면 wal_checkpoint 시도 후 WAL/SHM 정리
        # checkpoint가 완전히 끝났을 때만 -wal/-shm을 지운다. 실패를 무시하고
        # 지우면 미반영 커밋이 사라지고, 이후 교체까지 실패하면
        # 운영 DB가 WAL 없이 스테일로 남는다. (ISSUE-002)
        checkpoint_done = False
        if target_db_path.exists():
            conn = None
            try:
                conn = sqlite3.connect(str(target_db_path), timeout=2.0)
                row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
                # (busy, log, checkpointed). busy != 0이거나 남은 프레임이
                # 있으면 다른 연결이 읽는 중이라 끝까지 못 비운 상태다.
                # WAL 모드가 아닌 DB는 log == checkpointed == -1을 돌려줘 지울대상이 없다.
                if row is not None and int(row[0]) == 0 and int(row[1]) == int(row[2]):
                    checkpoint_done = True
            except Exception as e:
                print(f"[DB 스왑 알림] WAL checkpoint 확인 실패: {e}")
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            if checkpoint_done:
                wal_file = target_db_path.with_name(f"{target_db_path.name}-wal")
                shm_file = target_db_path.with_name(f"{target_db_path.name}-shm")
                for f in (wal_file, shm_file):
                    if f.exists():
                        try:
                            f.unlink()
                        except Exception:
                            pass
            else:
                print("[DB 스왑 알림] checkpoint 미완료: -wal/-shm을 남기고 교체합니다.")

        # 2. 재시도 루프 (os.replace → SQLite online backup)
        # 열려 있는 DB 파일 위에 파일 바이트를 복사(shutil.copy2)하지 않는다. 지우지 못한 이전
        # DB의 -wal이 남아 있으면 다음 연결이 그 프레임을 새 파일에 재생해 DB가 손상될 수 있다.
        # backup API는 SQLite가 잠금과 WAL을 스스로 처리한다. (감사 R3-18)
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                os.replace(tmp_db_path, target_db_path)
                return
            except Exception as replace_error:
                last_error = replace_error
            if target_db_path.exists():
                src_conn = None
                dst_conn = None
                try:
                    src_conn = sqlite3.connect(str(tmp_db_path), timeout=2.0)
                    dst_conn = sqlite3.connect(str(target_db_path), timeout=5.0)
                    if not DatabaseManager._target_writable(target_db_path):
                        raise PermissionError(
                            f"대상 DB({target_db_path})가 잠겨 있어 교체를 대기합니다."
                        )
                    src_conn.backup(dst_conn)
                    src_conn.close()
                    src_conn = None
                    tmp_db_path.unlink(missing_ok=True)
                    return
                except Exception as backup_error:
                    last_error = backup_error
                finally:
                    for c in (dst_conn, src_conn):
                        if c:
                            try:
                                c.close()
                            except Exception:
                                pass
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)

        # 재시도 초과 실패 시 tmp_db를 보존하며 명확한 안내 출력
        err_msg = (
            f"[DB 스왑 경고] 대상 DB 파일({target_db_path})이 다른 프로세스에 의해 "
            f"잠겨 있어 {max_retries}회 재시도 후에도 교체하지 못했습니다 ({last_error}).\n"
            f"생성된 최신 데이터는 임시 파일에 안전하게 보존되었습니다: {tmp_db_path}\n"
            f"웹 서버를 일시 종료하거나 파일 잠금이 해제된 후 교체하십시오."
        )
        print(err_msg, file=sys.stderr)
        raise PermissionError(err_msg) from last_error
