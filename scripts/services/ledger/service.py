# -*- coding: utf-8 -*-
import os
import sys
import re
import time
import shutil
import json
import sqlite3
import hashlib
from db.ledger_outbox import enqueue as enqueue_excel
import datetime
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BASE_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_BASE_DIR / "data_requests.db"
DEFAULT_JSON_PATH = DEFAULT_BASE_DIR / "data_requests.json"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
from search_query import (
    _expand_terms,
    build_fts_match,
    fields_match,
    parse_query,
    plan_is_empty,
    row_matches,
    score_row,
    strip_particle,
    NOSPACE_CODEPOINTS,
)
from db import fts_spec
from pipeline_guard import PipelineGuard
from extractors.text_extractor import mask_pii
from write_guard import ensure_writable
from services.ledger.due import DUE_FILTERS, ledger_due_state, matches_due_filter
from services.ledger.ids import LEDGER_TEXT_FIELDS, _clean, next_ledger_id, row_fingerprint
from services.ledger.autolink import auto_link_ledger_to_doc
from services.ledger.history import build_ledger_timeline
from extractors.ledger.dates import coerce_ledger_date

def _history_actor() -> str:
    """이력에 남길 수행자. 이 시스템은 로그인이 없으므로 OS 계정명을 쓴다(제안서 D7).

    누가 고쳤는지 아무 흔적도 없는 것보다는 낫고, 개인정보 수집도 아니다.
    """
    import getpass
    try:
        return getpass.getuser()[:60]
    except Exception:
        return ""


def _valid_ledger_date(value, year_str: str = "") -> bool:
    """웹 CRUD 날짜 검증. 빈 값 또는 `YYYY-MM-DD`로 읽을 수 있는 실재 날짜만 허용.

    엑셀 파서와 같은 표기(`2026.9.10` 등)를 받는다. 정규화는 `coerce_ledger_date`가 한다.
    """
    return coerce_ledger_date(value, year_str) is not None


def _now_str() -> str:
    """Write timestamp with microseconds (ISSUE-001).

    updated_at doubles as the optimistic-locking version. Second
    precision lets two writes in the same second share one version
    string, so the second silently overwrites the first. Fixed width
    keeps existing lexicographic comparisons (merge ua >= started_at)
    ordered and mixes safely with older second-precision values.
    """
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")



JSON_LOCK = threading.Lock()
LAST_SYNC_THREAD = None
_JSON_STATE_LOCK = threading.Lock()
_JSON_RUNNING = {}
_JSON_DIRTY = {}
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = {}
_SEARCH_LOG_PRUNE_LOCK = threading.Lock()
_LAST_SEARCH_LOG_PRUNE = {}
_SEARCH_LOG_PRUNE_INTERVAL_SECONDS = 24 * 3600
def _db_file_identity(db_path):
    try:
        st = Path(db_path).stat()
    except OSError:
        return None
    return (st.st_ino, st.st_dev, getattr(st, "st_birthtime", st.st_ctime))

class LedgerService:
    """Encapsulates all database queries and business logic for the Request Ledger system."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
        base_dir: Optional[Path] = None,
        read_only: bool = False,
        immutable: bool = False,
    ):
        # base_dir를 생략하면 DB가 있는 폴더를 쓴다. 예전에는 운영 폴더가 기본값이라,
        # DB만 임시 파일로 바꾼 테스트가 운영 마스터 엑셀·보류 큐에 썼다. (감사 R3-01)
        if base_dir:
            self.base_dir = Path(base_dir)
        elif db_path:
            self.base_dir = Path(db_path).resolve().parent
        else:
            self.base_dir = DEFAULT_BASE_DIR
        self.db_path = Path(db_path) if db_path else (self.base_dir / "data_requests.db")
        self.json_path = Path(json_path) if json_path else (self.base_dir / "data_requests.json")
        # read_only: agent CLI/MCP 같은 조회 전용 진입점용. 스키마 준비·마이그레이션·
        # 검색 로그를 포함한 모든 쓰기를 막는다. 스키마 확인은 호출자(Facade)가 먼저 한다.
        # (감사 ISSUE-002)
        self._read_only = bool(read_only)
        self._immutable = bool(immutable)

    def get_conn(self, readonly: bool = False) -> sqlite3.Connection:
        # 검색 경로는 읽기 전용으로 연다(제안서 D9). DB가 아직 없으면 mode=ro가 실패하므로
        # 그때는 평소대로 연다. 스키마 준비가 먼저 돌면서 파일을 만든다.
        # read_only 서비스에서는 쓰기 연결을 절대 열지 않는다. 호출자가 스키마를 먼저
        # 확인했으므로 없는 DB를 만드는 일도 없다. (감사 ISSUE-002)
        if (readonly or self._read_only) and Path(self.db_path).exists():
            try:
                from extractors.ledger.workbook import connect_readonly as _ro
                # -wal이 없으면 immutable로 열어 사이드카 생성 자체를 막는다.
                # (Facade._readonly_conn과 같은 규칙. 감사 ISSUE-002)
                wal_path = Path(str(self.db_path) + "-wal")
                try:
                    has_wal = wal_path.exists()
                except OSError:
                    has_wal = True
                conn = _ro(self.db_path,
                           immutable=self._immutable or not has_wal)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout = 5000;")
                conn.execute("PRAGMA cache_size = -32000;")
                conn.execute("PRAGMA query_only = ON;")
                return conn
            except sqlite3.Error:
                if self._read_only:
                    raise
                pass
        if self._read_only:
            # 없는 DB를 만들지 않는다. 호출자의 _require_schema가 먼저 거절했어야 하며,
            # 여기까지 왔다면 읽기 전용 계약을 어기지 않고 실패한다.
            raise sqlite3.OperationalError("읽기 전용 서비스에 DB 파일이 없습니다.")
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000;")
        # journal_mode는 파일에 영구 기록되는 설정이라 스키마 준비(_ensure_schema) 때 한 번만 건다.
        # 조회 연결마다 걸면 읽기만 하는 요청도 쓰기 잠금을 시도한다. (감사 R4-15g)
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA cache_size = -32000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _ensure_schema(self):
        """DB 스키마를 준비한다. 같은 DB 파일에는 프로세스당 한 번만 DDL을 실행한다.

        예전에는 검색·조회 요청마다 테이블·FTS·트리거·인덱스 DDL 30여 개와 커밋을 실행했다.
        파이프라인이 DB를 교체하면 파일 식별자(inode·생성 시각)가 바뀌므로 그때 다시 확인한다.
        read_only 서비스에서는 아무것도 만들지 않는다. 스키마 확인은 호출자가 먼저 하며,
        여기서는 DDL·journal_mode 변경·마이그레이션을 모두 건너뛴다. (감사 ISSUE-002)
        """
        if self._read_only:
            return
        from db.database_manager import DatabaseManager
        key = str(Path(self.db_path).resolve())
        ident = _db_file_identity(self.db_path)
        if ident is not None and _SCHEMA_READY.get(key) == ident:
            return
        conn = self.get_conn()
        try:
            # ensure_schema checks the version before changing journal mode/DDL.
            DatabaseManager.ensure_schema(conn)
        finally:
            conn.close()
        with _SCHEMA_LOCK:
            _SCHEMA_READY[key] = _db_file_identity(self.db_path)

    def auto_link(self, conn: sqlite3.Connection, item: Dict[str, Any]) -> str:
        return auto_link_ledger_to_doc(conn, item)

    def _guard_db_write(self):
        if self._read_only:
            raise RuntimeError("읽기 전용 서비스에서는 쓸 수 없습니다.")
        ensure_writable(self.db_path, "SQLite DB")

    def sync_json_file(self, async_mode: bool = True):
        """data_requests.json을 SQLite 기준으로 다시 쓴다.

        비동기 호출이 연달아 들어오면 한 번의 재작성으로 합친다. JSON은 문서 전문을 포함해
        100MB를 넘을 수 있어, 대장 수정마다 스레드를 새로 띄우면 같은 파일을 줄지어
        다시 쓰게 된다. (감사 R3-16)
        """
        global LAST_SYNC_THREAD

        def _do_sync():
            with JSON_LOCK:
                if not self.db_path.exists():
                    return
                ensure_writable(self.json_path, "JSON 캐시")
                # 행 단위 스트리밍으로 쓴다. 예전에는 세 테이블을 통째로 리스트로 읽고 indent=2로
                # 다시 써서, DB가 커지면 대장 한 건 수정마다 전문 크기만큼 메모리를 썼다.
                # 한 읽기 트랜잭션 안에서 읽어 세 테이블이 같은 시점을 본다.
                conn = None
                try:
                    from db.json_export import export_combined_json
                    conn = self.get_conn(readonly=True)
                    conn.execute("BEGIN")
                    export_combined_json(conn, self.json_path)
                    print("✓ data_requests.json 동기화 완료")
                except Exception as e:
                    if self.db_path.exists():
                        print(f"JSON 동기화 알림: {e}")
                finally:
                    if conn:
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                        conn.close()

        if not async_mode:
            _do_sync()
            return None

        key = str(self.json_path.resolve())
        with _JSON_STATE_LOCK:
            if _JSON_RUNNING.get(key):
                # 이미 재작성 중이면 끝난 뒤 한 번 더 쓰도록 표시만 한다.
                _JSON_DIRTY[key] = True
                return LAST_SYNC_THREAD
            _JSON_RUNNING[key] = True
            _JSON_DIRTY[key] = False

        def _runner():
            try:
                while True:
                    try:
                        _do_sync()
                    except Exception as e:
                        print(f"JSON 동기화 알림: {e}")
                    with _JSON_STATE_LOCK:
                        if not _JSON_DIRTY.get(key):
                            _JSON_RUNNING[key] = False
                            return
                        _JSON_DIRTY[key] = False
            finally:
                with _JSON_STATE_LOCK:
                    _JSON_RUNNING[key] = False

        t = threading.Thread(target=_runner, daemon=True)
        LAST_SYNC_THREAD = t
        t.start()
        return t

    @staticmethod
    def wait_last_sync(timeout: float = 3.0):
        if LAST_SYNC_THREAD and LAST_SYNC_THREAD.is_alive():
            LAST_SYNC_THREAD.join(timeout=timeout)

    def get_stats(self):
        try:
            self._ensure_schema()
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                doc_count = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                qa_count = cur.execute("SELECT COUNT(*) FROM qa_items").fetchone()[0]
                # 화면·산출물과 같은 기준으로 삭제 tombstone은 세지 않는다.
                ledger_count = cur.execute(
                    "SELECT COUNT(*) FROM request_ledger WHERE COALESCE(status, '') != '[삭제]'"
                ).fetchone()[0]
                return {
                    "success": True,
                    "documents": doc_count,
                    "qa_items": qa_count,
                    "request_ledger": ledger_count
                }
            finally:
                conn.close()
        except Exception as e:
            return {"success": False, "documents": 0, "qa_items": 0, "request_ledger": 0, "error": str(e)}

    # 검색 대상별 설정. FTS 컬럼·가중치는 fts_spec이 정본이고, 여기서는 나머지만 정한다.
    # 예전에는 이 표의 text_fields에 FTS에 없는 컬럼(doc_number·seq_no)이 섞여 있어
    # FTS 경로와 대체 경로가 서로 다른 필드를 봤다(제안서 S3).
    _SEARCH_SPECS = {
        "ledger": {
            "table": "request_ledger l",
            "fts": "request_ledger_fts",
            "where": "COALESCE(l.status, '') != '[삭제]'",
            "order": "l.year DESC, l.request_date DESC, l.seq_no DESC",
            # (컬럼, 관련도 가중치). 대시보드 클라이언트 검색과 같은 배점이다.
            "weights": (("title", 50), ("details", 35), ("requester", 25),
                        ("party", 25), ("seq_no", 10), ("note", 10)),
            "snippet_field": "details",
        },
        "docs": {
            "table": "documents l",
            "fts": "documents_fts",
            "where": "1=1",
            "order": "l.year DESC, l.request_date DESC, l.doc_id ASC",
            "weights": (("title", 50), ("requester", 25), ("doc_number", 10),
                        ("question_list", 20), ("full_markdown", 10)),
            "snippet_field": "full_markdown",
        },
        "qa": {
            "table": "qa_items l",
            "fts": "qa_items_fts",
            "where": "1=1",
            "order": "l.year DESC, l.request_date DESC, l.qa_id ASC",
            "weights": (("question_title", 50), ("requester", 25), ("answer_markdown", 10)),
            "snippet_field": "answer_markdown",
        },
    }
    # /api/search 목록 응답에서 뺄 대용량 필드. 전문은 /api/documents/<id>로 받는다. (감사 R3-16)
    _HEAVY_FIELDS = ("full_markdown", "answer_full_text", "answer_markdown", "answer_full")
    # 스니펫 길이(제안서 S5). 목록 카드에 들어갈 만큼만 자른다.
    _SNIPPET_RADIUS = 60

    # 테이블별 컬럼 목록 캐시. PRAGMA table_info를 조회마다 돌 필요가 없다.
    _COLUMN_CACHE = {}

    @classmethod
    def _select_columns(cls, cur, table_name, slim):
        """조회할 컬럼 목록. slim이면 본문 컬럼을 처음부터 빼고 읽는다.

        예전에는 `SELECT l.*`로 전부 읽은 뒤 `_HEAVY_FIELDS`를 버렸다. 목록 응답 한 번에
        수십 MB를 읽어 객체로 만들고 곧바로 버리는 비용이었다.
        """
        cols = cls._COLUMN_CACHE.get(table_name)
        if cols is None:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table_name})")]
            cls._COLUMN_CACHE[table_name] = cols
        if not slim:
            return "l.*"
        keep = [c for c in cols if c not in cls._HEAVY_FIELDS]
        return ", ".join("l." + c for c in keep) if keep else "l.*"

    @classmethod
    def _spec(cls, kind):
        spec = cls._SEARCH_SPECS[kind]
        fts_name = spec["fts"]
        text_fields = fts_spec.FTS_SPECS[fts_name][1]
        cho_fields = fts_spec.FTS_CHOSEONG_FIELDS[fts_name]
        return spec, fts_name, text_fields, cho_fields

    def _make_snippet(self, plan, text, use_synonyms=False):
        """검색어가 맞은 자리 앞뒤를 잘라 돌려준다. FTS·대체 경로가 같은 모양을 낸다.

        FTS5의 snippet()은 대체 경로에 없다. 경로에 따라 스니펫이 있었다 없었다 하면
        화면이 들쭉날쭉해지므로, 파이썬에서 한 가지 방식으로만 만든다(제안서 S5).
        """
        body = str(text or "")
        if not body:
            return ""
        low = body.lower()
        needles = [str(ph).lower() for ph in (plan.get("phrases") or ())]
        for terms in plan.get("groups") or ():
            for t in terms:
                if not re.fullmatch(r"[ㄱ-ㅎ]+", t):
                    needles.append(t.lower())
        hit = -1
        for n in needles:
            if not n:
                continue
            hit = low.find(n)
            if hit >= 0:
                break
        if hit < 0:
            head = body[: self._SNIPPET_RADIUS * 2].strip()
            return head + ("\u2026" if len(body) > len(head) else "")
        start = max(0, hit - self._SNIPPET_RADIUS)
        end = min(len(body), hit + self._SNIPPET_RADIUS)
        out = body[start:end].strip()
        return ("\u2026" if start > 0 else "") + out + ("\u2026" if end < len(body) else "")

    def _log_search(self, kind, kw, count, elapsed_ms, used_fts):
        """검색 로그(제안서 S9). 0건으로 끝난 질의를 알아야 동의어 사전을 고칠 수 있다.

        로그 실패는 검색 실패가 아니다. 어떤 예외도 삼킨다.
        read_only 서비스에서는 로그 쓰기 자체를 하지 않는다. (감사 ISSUE-002)
        """
        if self._read_only:
            return
        if not kw:
            return
        try:
            conn = self.get_conn()
            try:
                conn.execute(
                    "INSERT INTO search_log (query, mode, result_count, elapsed_ms, used_fts, searched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(kw)[:300], kind, int(count), int(elapsed_ms),
                     1 if used_fts else 0, datetime.datetime.now().isoformat(timespec="seconds")),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        self._maybe_prune_search_log()

    def _maybe_prune_search_log(self):
        """검색 로그 rolling 정리. 프로세스·DB당 24시간에 한 번만 돈다. (감사 R6-04)

        로그 정리는 검색이 아니다. 실패해도 검색 결과에 영향을 주지 않는다.
        read_only 서비스에서는 정리 쓰기를 하지 않는다. (감사 ISSUE-002)
        """
        if self._read_only:
            return
        try:
            key = str(Path(self.db_path).resolve())
        except OSError:
            return
        now = time.time()
        with _SEARCH_LOG_PRUNE_LOCK:
            if now - _LAST_SEARCH_LOG_PRUNE.get(key, 0) < _SEARCH_LOG_PRUNE_INTERVAL_SECONDS:
                return
            _LAST_SEARCH_LOG_PRUNE[key] = now
        try:
            from db.database_manager import DatabaseManager
            conn = self.get_conn()
            try:
                deleted = DatabaseManager.prune_search_log(conn)
                if deleted:
                    print(f"오래된 검색 로그 {deleted}건 정리")
            finally:
                conn.close()
        except Exception:
            pass

    def _search(self, kind, year=None, kw=None, slim=False, use_synonyms=True, with_snippet=False):
        spec, fts_table, text_fields, cho_fields = self._spec(kind)
        table_sql, base_where, order_by = spec["table"], spec["where"], spec["order"]
        weights = fts_spec.weights_sql(fts_table)
        self._ensure_schema()
        started = time.time()
        used_fts = False
        rows = []
        conn = self.get_conn(readonly=True)
        try:
            cur = conn.cursor()
            kw = (kw or '').strip()
            year_filter = ""
            params = []
            if year and year != 'ALL':
                year_filter = " AND l.year = ?"
                params.append(str(year))

            plan = parse_query(kw) if kw else None
            # 스니펫은 본문에서 잘라내므로, 그때는 본문을 읽어야 한다. 잘라낸 뒤 slim을 적용한다.
            read_slim = slim and not with_snippet
            if kw and plan is not None and not plan_is_empty(plan):
                fts_exists = cur.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (fts_table,)
                ).fetchone()
                fts_kw = build_fts_match(kw, use_synonyms=use_synonyms) if fts_exists else ""
                if fts_kw:
                    try:
                        sel = self._select_columns(cur, fts_spec.FTS_SPECS[fts_table][0], read_slim)
                        fts_sql = f"""
                            SELECT {sel}, bm25({fts_table}, {weights}) AS rank
                            FROM {table_sql}
                            JOIN {fts_table} ON l.rowid = {fts_table}.rowid
                            WHERE {fts_table} MATCH ? AND {base_where} {year_filter}
                            ORDER BY rank ASC, {order_by}
                        """
                        rows = [dict(r) for r in cur.execute(fts_sql, [fts_kw] + params).fetchall()]
                        used_fts = bool(rows)
                    except sqlite3.Error:
                        rows = []

                # FTS로 표현할 수 없는 문법(초성·띄어쓰기 무시·조사·2글자)이거나 trigram이
                # 못 찾으면 파이썬으로 평가한다. 예전에는 검색어 전체를 LIKE 한 덩어리로
                # 비교해 OR·제외어·초성 검색이 결과 0건이었다. (감사 R3-03)
                if not rows:
                    rows = self._fallback_search(cur, kind, kw, year_filter, params, use_synonyms, read_slim)
            else:
                sel = self._select_columns(cur, table_sql.split()[0], read_slim)
                sql = f"SELECT {sel}, 0 AS rank FROM {table_sql} WHERE {base_where} {year_filter} ORDER BY {order_by}"
                rows = [dict(r) for r in cur.execute(sql, params).fetchall()]

            # 필드 한정자(요구자:·상태:·기간: 등)는 어느 경로로 왔든 마지막에 같은 규칙으로 건다.
            if plan is not None and (plan.get("fields") or {}):
                rows = [r for r in rows if fields_match(plan, r)]

            if with_snippet and plan is not None:
                for rec in rows:
                    rec["snippet"] = self._make_snippet(plan, rec.get(spec["snippet_field"]), use_synonyms)

            if slim:
                for rec in rows:
                    for f in self._HEAVY_FIELDS:
                        rec.pop(f, None)
            return rows
        finally:
            conn.close()
            self._log_search(kind, kw, len(rows), (time.time() - started) * 1000, used_fts)

    _FALLBACK_FETCH_CHUNK = 500

    def _fallback_search(self, cur, kind, kw, year_filter, params, use_synonyms=True, slim=False):
        """FTS로 못 찾거나 표현할 수 없는 질의를 파이썬으로 평가한다.

        예전에는 SELECT l.* 로 전문(full_markdown 포함)을 전 행 읽었다. 이제 비교에 필요한 열만
        훑고, 초성만으로 된 질의면 짧은 초성 비교 열(제목·요구자)만 읽는다. 일치한 행만
        rowid로 다시 조회해 돌려준다. (감사 R4-09)

        여기에 더해 평문 토큰이 있으면 SQL LIKE로 후보를 먼저 줄인다(제안서 S2). 인덱스는
        못 타지만 C 레벨 스캔이라 파이썬 루프보다 자릿수가 빠르다. 결과가 달라지면 안 되므로
        OR 그룹이 하나이고 초성·제외어가 섞이지 않은 질의에서만 건다.
        """
        spec, _fts, text_fields, cho_fields = self._spec(kind)
        table_sql, base_where, order_by = spec["table"], spec["where"], spec["order"]
        plan = parse_query(kw)
        jamo_only = (
            not plan["phrases"] and not plan["neg"] and plan["groups"]
            and all(re.fullmatch(r"[ㄱ-ㅎ]+", t) for terms in plan["groups"] for t in terms)
        )
        scan_fields = list(dict.fromkeys(cho_fields if jamo_only else (list(text_fields) + list(cho_fields))))

        cols = ", ".join("l." + f for f in scan_fields)
        prefilter_where, prefilter_params = self._build_prefilter(plan, jamo_only, use_synonyms)
        if prefilter_where:
            # 정규화 블롭을 행당 한 번만 만들고(MATERIALIZED), 그 위에서 후보를 고른다.
            # CTE 안에서 base_where·year_filter를 이미 걸었으므로 바깥에서 다시 걸지 않는다.
            norm_expr = self._norm_expr(text_fields)
            scan_sql = (
                f"WITH _norm AS MATERIALIZED ("
                f"  SELECT l.rowid AS _rid, {norm_expr} AS _n"
                f"  FROM {table_sql} WHERE {base_where} {year_filter}"
                f") "
                f"SELECT l.rowid AS _rid, {cols} FROM {table_sql} "
                f"WHERE l.rowid IN (SELECT _rid FROM _norm WHERE {prefilter_where}) "
                f"ORDER BY {order_by}"
            )
            scan_params = list(params) + prefilter_params
        else:
            scan_sql = (
                f"SELECT l.rowid AS _rid, {cols} "
                f"FROM {table_sql} WHERE {base_where} {year_filter} ORDER BY {order_by}"
            )
            scan_params = list(params)
        matched = []
        scores = {}
        weight_map = dict(spec["weights"])
        body_fields = {spec["snippet_field"]}
        score_fields = [f for f in scan_fields if f in weight_map and f not in body_fields]
        for r in cur.execute(scan_sql, scan_params):
            texts = [] if jamo_only else [r[f] for f in text_fields]
            if row_matches(plan, texts, [r[f] for f in cho_fields], use_synonyms):
                rid = r["_rid"]
                matched.append(rid)
                # 관련도 점수(제안서 S7). 예전에는 전부 1.0이라 정렬이 날짜순뿐이었다.
                # 배점은 짧은 필드에서만 낸다. 본문(full_markdown·answer_markdown)은
                # 가중치가 가장 낮은데도 행마다 수만 자를 다시 정규화해, 예전에는 배점
                # 하나가 검색 전체보다 오래 걸렸다. 순위는 제목·요구자·질문이 가른다.
                scores[rid] = score_row(
                    plan,
                    [(r[f], weight_map[f]) for f in score_fields],
                    use_synonyms,
                )
        if not matched:
            return []
        # 점수가 높은 순, 같으면 원래 정렬(연도·날짜 역순)을 지킨다.
        order_index = {rid: i for i, rid in enumerate(matched)}
        matched.sort(key=lambda rid: (-scores.get(rid, 0), order_index[rid]))
        by_rid = {}
        refetch_cols = self._select_columns(cur, table_sql.split()[0], slim)
        for start in range(0, len(matched), self._FALLBACK_FETCH_CHUNK):
            chunk = matched[start:start + self._FALLBACK_FETCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for r in cur.execute(
                f"SELECT l.rowid AS _rid, {refetch_cols} FROM {table_sql} "
                f"WHERE l.rowid IN ({placeholders})",
                chunk,
            ):
                rec = dict(r)
                rid = rec.pop("_rid")
                # bm25는 작을수록 관련도가 높다. 점수를 음수로 넣어 같은 방향으로 맞춘다.
                rec["rank"] = -float(scores.get(rid, 0))
                by_rid[rid] = rec
        return [by_rid[rid] for rid in matched if rid in by_rid]

    # 한 낱말이 이보다 많은 동의어로 퍼지면 조건이 과도해져 선필터가 오히려 비싸진다.
    _PREFILTER_MAX_VARIANTS = 24

    @staticmethod
    def _norm_expr(text_fields) -> str:
        """검색 필드를 이어 붙여 공백류를 뗀 SQL 식.

        파이썬 쪽 `_nospace_lower`와 같은 공백 기준이어야 선필터가 결과를 깎지
        않는다. 예전에는 공백·TAB·LF·CR 4종만 떼서, NBSP·수직탭·전각공백이 낱말 사이에
        낀 행을 선필터가 통째로 잘라냈다. (감사 R6-01)
        공백 집합은 `search.lang.NOSPACE_CODEPOINTS` 한 곳에서만 관리한다.
        한글에는 대소문자가 없고 LIKE는 ASCII를 대소문자 구분 없이 비교하므로 lower()는 생략한다.
        """
        joined = " || ".join(f"COALESCE(l.{f}, '')" for f in text_fields)
        expr = joined
        for cp in NOSPACE_CODEPOINTS:
            expr = f"REPLACE({expr}, CHAR({cp}), '')"
        return expr

    @staticmethod
    def _build_prefilter(plan, jamo_only, use_synonyms):
        """파이썬이 볼 행을 줄이는 SQL 조건을 만든다 (제안서 S2).

        **결과가 줄어들면 안 된다.** 이 조건은 정답의 상위집합만 만들어야 하고, 정확한
        판정은 뒤에서 `row_matches`가 한다.

        비교 대상은 `_norm_expr`가 만든 **공백을 뗀 블롭 하나**다. 파이썬 평가가 띄어쓰기를
        무시하므로 원문에 `국 회`처럼 갈라진 낱말도 `국회` 질의에 맞는데, 원시 LIKE만
        걸면 그런 행이 통째로 사라진다(운영 질의가 72건에서 70건이 됐다). 반대로 붙어 있는
        낱말은 공백을 떼도 그대로라, 이 블롭 하나가 두 경우를 모두 잡는다.

        동의어가 켜지면 변형을 모두 OR로 묶는다. 묶어도 여전히 상위집합이다. 예전에는
        동의어가 켜졌다는 이유만으로 선필터를 통째로 껐고, 기본값이 '켬'이라 2글자 질의가
        늘 전수 스캔이었다.

        제외어(`-단어`)와 구문(`"..."`)은 조건에 넣지 않는다. 긍정 토큰으로만 좁힌 집합은
        상위집합이므로, 그것들을 파이썬이 뒤에 걸어도 빠지는 행이 없다.

        초성 질의는 SQL로 표현할 수 없어 좁히지 않는다(대신 짧은 열만 훑어 이미 빠르다).
        """
        if jamo_only:
            return "", []
        groups = plan.get("groups") or ()
        if not groups:
            return "", []

        group_clauses = []
        sql_params = []
        for group in groups:
            terms = [t for t in group if not re.fullmatch(r"[ㄱ-ㅎ]+", t)]
            if not terms or len(terms) != len(group):
                # 초성이 섞인 그룹은 좁힐 수 없다. 한 그룹만 좁히면 다른 그룹의 행이 사라진다.
                return "", []
            term_clauses = []
            for t in terms:
                # 조사 분리는 파이썬이 처리한다. SQL은 어간으로만 좁혀 더 넓게 남긴다.
                stem = strip_particle(t) or t
                variants = {stem, t}
                if use_synonyms:
                    # row_matches가 보는 것과 같은 변형 집합이어야 결과가 줄지 않는다.
                    variants.update(_expand_terms(stem, True))
                    variants.update(_expand_terms(t, True))
                variants = {v for v in variants if v}
                if len(variants) > LedgerService._PREFILTER_MAX_VARIANTS:
                    return "", []
                term_clauses.append("(" + " OR ".join(["_n LIKE ?"] * len(variants)) + ")")
                sql_params.extend("%" + v + "%" for v in sorted(variants))
            group_clauses.append("(" + " AND ".join(term_clauses) + ")")
        return "(" + " OR ".join(group_clauses) + ")", sql_params

    def query_ledger(self, year=None, kw=None, status=None, due=None, today=None,
                     use_synonyms=True, with_snippet=False):
        """관리대장 조회. `status`: 진행상태 값, `due`: soon(7일 이내) / overdue(기한 경과) / open(미제출)."""
        rows = self._search("ledger", year=year, kw=kw,
                            use_synonyms=use_synonyms, with_snippet=with_snippet)
        status = str(status or "").strip()
        if status and status != "ALL":
            rows = [r for r in rows if str(r.get("status") or "").strip() == status]
        if due in DUE_FILTERS:
            rows = [r for r in rows if matches_due_filter(r, due, today)]
        return rows

    def ledger_summary(self, year=None, today=None):
        """진행상태별 건수와 마감 현황(기한 경과·7일 이내·미제출).

        상태별 집계는 SQL GROUP BY로 센다. 예전에는 전 행을 파이썬으로 끌어와
        `details`·`note`까지 통째로 읽고 셌다(제안서 D4).

        마감 판정만은 파이썬(`ledger_due_state`)에 남긴다. 불변조건 19에 따라 대시보드
        `ledgerDueInfo`와 같은 규칙을 써야 하고, 그 규칙을 SQL로 옮기면 두 벌이 된다.
        마감 판정에 필요한 열만 읽어 비용을 줄인다.
        """
        self._ensure_schema()
        conn = self.get_conn(readonly=True)
        try:
            cur = conn.cursor()
            where = "COALESCE(status, '') != '[삭제]'"
            params = []
            if year and year != "ALL":
                where += " AND year = ?"
                params.append(str(year))

            by_status = {}
            total = 0
            for row in cur.execute(
                f"SELECT COALESCE(NULLIF(TRIM(status), ''), '(없음)') AS st, COUNT(*) AS c "
                f"FROM request_ledger WHERE {where} GROUP BY st", params
            ):
                by_status[row["st"]] = row["c"]
                total += row["c"]

            due = {"overdue": 0, "soon": 0, "open": 0}
            for row in cur.execute(
                f"SELECT status, deadline, submit_date FROM request_ledger WHERE {where}", params
            ):
                state, _ = ledger_due_state(dict(row), today)
                if state == "overdue":
                    due["overdue"] += 1
                elif state == "soon":
                    due["soon"] += 1
                if state != "done":
                    due["open"] += 1
        finally:
            conn.close()
        return {"success": True, "total": total, "by_status": by_status, "due": due}

    def query_documents(self, year=None, kw=None, slim=False, use_synonyms=True, with_snippet=False):
        return self._search("docs", year=year, kw=kw, slim=slim,
                            use_synonyms=use_synonyms, with_snippet=with_snippet)

    def query_qa_items(self, year=None, kw=None, slim=False, use_synonyms=True, with_snippet=False):
        return self._search("qa", year=year, kw=kw, slim=slim,
                            use_synonyms=use_synonyms, with_snippet=with_snippet)

    def search_all(self, kw='', year=None, search_type='all', slim=False,
                   use_synonyms=True, with_snippet=False):
        ledger_res = []
        docs_res = []
        qa_res = []

        opts = {"use_synonyms": use_synonyms, "with_snippet": with_snippet}
        if search_type in ('all', 'ledger'):
            ledger_res = self.query_ledger(year=year, kw=kw, **opts)
        if search_type in ('all', 'docs'):
            docs_res = self.query_documents(year=year, kw=kw, slim=slim, **opts)
        if search_type in ('all', 'qa'):
            qa_res = self.query_qa_items(year=year, kw=kw, slim=slim, **opts)

        return {
            "success": True,
            "query": kw,
            "counts": {
                "ledger": len(ledger_res),
                "docs": len(docs_res),
                "qa": len(qa_res),
                "total": len(ledger_res) + len(docs_res) + len(qa_res)
            },
            "ledger": ledger_res,
            "documents": docs_res,
            "qa_items": qa_res
        }

    def suggest(self, prefix, limit=10):
        """자동완성 후보(제안서 S8). 요구자·정당·기관·태그처럼 값의 가짓수가 적은 열에서 뽑는다.

        오타가 줄면 0건 검색도 줄어든다. 본문은 후보로 쓰지 않는다(가짓수가 너무 많다).
        """
        prefix = str(prefix or "").strip()
        if len(prefix) < 1:
            return []
        self._ensure_schema()
        like = prefix.replace("%", "").replace("_", "") + "%"
        contains = "%" + prefix.replace("%", "").replace("_", "") + "%"
        sources = (
            ("requester", "request_ledger", "요구자"),
            ("party", "request_ledger", "정당"),
            ("requester", "documents", "요구자"),
            ("institution", "documents", "기관"),
            ("department", "request_ledger", "부서"),
        )
        seen = {}
        conn = self.get_conn(readonly=True)
        try:
            cur = conn.cursor()
            for column, table, label in sources:
                try:
                    rows = cur.execute(
                        f"SELECT {column} AS v, COUNT(*) AS c FROM {table} "
                        f"WHERE {column} IS NOT NULL AND TRIM({column}) != '' "
                        f"AND ({column} LIKE ? OR {column} LIKE ?) "
                        f"GROUP BY v ORDER BY c DESC LIMIT ?",
                        (like, contains, int(limit)),
                    ).fetchall()
                except sqlite3.Error:
                    continue
                for r in rows:
                    value = str(r["v"]).strip()
                    key = (label, value)
                    if key not in seen:
                        seen[key] = {"value": value, "field": label, "count": r["c"]}
        finally:
            conn.close()
        out = sorted(seen.values(), key=lambda x: (-x["count"], x["value"]))
        # 접두사 일치를 부분 일치보다 앞세운다.
        out.sort(key=lambda x: 0 if x["value"].lower().startswith(prefix.lower()) else 1)
        return out[: int(limit)]

    def search_insights(self, limit=20, days=90):
        """검색 로그 요약(제안서 S9). 0건으로 끝난 질의와 느린 질의를 보여준다."""
        self._ensure_schema()
        since = (datetime.datetime.now() - datetime.timedelta(days=int(days))).isoformat(timespec="seconds")
        conn = self.get_conn(readonly=True)
        try:
            cur = conn.cursor()
            try:
                zero = [dict(r) for r in cur.execute(
                    "SELECT query, COUNT(*) AS hits, MAX(searched_at) AS last_at FROM search_log "
                    "WHERE result_count = 0 AND searched_at >= ? "
                    "GROUP BY query ORDER BY hits DESC, last_at DESC LIMIT ?", (since, int(limit)))]
                popular = [dict(r) for r in cur.execute(
                    "SELECT query, COUNT(*) AS hits FROM search_log WHERE searched_at >= ? "
                    "GROUP BY query ORDER BY hits DESC LIMIT ?", (since, int(limit)))]
                slow = [dict(r) for r in cur.execute(
                    "SELECT query, MAX(elapsed_ms) AS worst_ms, AVG(elapsed_ms) AS avg_ms, "
                    "SUM(used_fts) AS fts_hits, COUNT(*) AS hits FROM search_log "
                    "WHERE searched_at >= ? GROUP BY query ORDER BY worst_ms DESC LIMIT ?",
                    (since, int(limit)))]
            except sqlite3.Error:
                return {"success": True, "zero_result": [], "popular": [], "slow": []}
        finally:
            conn.close()
        return {"success": True, "zero_result": zero, "popular": popular, "slow": slow}

    def get_ledger_item(self, ledger_id):
        self._ensure_schema()
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM request_ledger WHERE ledger_id = ? AND COALESCE(status, '') != '[삭제]'", (ledger_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def _begin_immediate(cur, attempts: int = 3):
        """쓰기 트랜잭션 시작. 경합 시 짧게 쉬고 재시도한다.

        BEGIN IMMEDIATE는 잠금만 잡고 쓰기 전이므로 재시도가 안전하다.
        본문 단계의 짧은 경합은 busy_timeout(5초)이 1차로 흡수한다.
        """
        last_error = None
        for i in range(max(1, attempts)):
            try:
                cur.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as e:
                last_error = e
                time.sleep(0.1 * (i + 1))
        assert last_error is not None
        raise last_error

    def _reject_if_pipeline_running(self):
        guard = PipelineGuard(self.base_dir)
        if guard.is_locked() and not guard.is_mine():
            return {"success": False, "code": "pipeline_busy", "error": "문서 동기화 파이프라인이 실행 중입니다. 완료 후 다시 시도해주세요."}
        return None

    def _bump_live_version(self):
        """Notify other dashboard tabs even when no master Excel is configured."""
        try:
            from services.excel_sync_service import ExcelSyncService
            ExcelSyncService.get_instance(self.base_dir).bump_version()
        except Exception:
            pass

    def insert_ledger_item(self, data):
        blocked = self._reject_if_pipeline_running()
        if blocked:
            return blocked
        if not isinstance(data, dict):
            return {"success": False, "error": "요청 본문은 JSON 객체여야 합니다."}
        self._guard_db_write()
        self._ensure_schema()
        title = _clean(data.get('title'))
        requester = _clean(data.get('requester'))
        if not title:
            return {"success": False, "error": "요구자료명(제목)은 필수 입력 항목입니다."}
        if not requester:
            return {"success": False, "error": "요구자(의원실)는 필수 입력 항목입니다."}
        year = _clean(data.get('year')) or str(datetime.datetime.now().year)
        if not re.fullmatch(r'\d{4}', year):
            # ledger_id(REQ-YYYY-NNN)가 4자리 연도를 전제한다.
            return {"success": False, "error": "연도는 4자리 숫자여야 합니다."}
        # 엑셀 파서와 같은 표기(`2026.9.10` 등)를 받아 `YYYY-MM-DD`로 저장한다.
        dates = {}
        for _date_field in self._DATE_FIELDS:
            coerced = coerce_ledger_date(data.get(_date_field), year)
            if coerced is None:
                return {"success": False, "error": f"날짜는 YYYY-MM-DD 형식이어야 합니다: {_date_field}"}
            dates[_date_field] = coerced
        now_str = _now_str()
        curr_cfg = system_config.get_config(self.base_dir)
        default_dept = curr_cfg.get("department_name", "자료요구담당부서")
        requested_id = _clean(data.get('ledger_id'))
        # 자동 채번이 ID 접두사(REQ-YYYY-)의 번호로 다음 번호를 구하므로
        # 사용자가 지정한 ID도 REQ-YYYY-NNN 형식을 지켜야 한다.
        if requested_id and not re.fullmatch(r'REQ-\d{4}-\d+', requested_id):
            return {"success": False, "error": f"관리번호는 REQ-연도-번호 형식이어야 합니다 (예: REQ-{year}-001): {requested_id}"}
        request_key = _clean(data.get("idempotency_key"))
        if len(request_key) > 200:
            return {"success": False, "error": "등록 요청 키가 너무 깁니다."}
        fingerprint = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "idempotency_key"},
                                                sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        item = None
        last_error = None
        # BEGIN IMMEDIATE makes the number lookup and INSERT one serialized write unit.
        # Retry only auto-generated IDs; a user-supplied duplicate remains an error.
        for _attempt in range(3):
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                self._begin_immediate(cur)
                if request_key:
                    receipt = cur.execute("SELECT fingerprint, ledger_id FROM ledger_receipts WHERE request_key=?", (request_key,)).fetchone()
                    if receipt:
                        if receipt[0] != fingerprint:
                            return {"success": False, "code": "conflict", "error": "같은 등록 요청 키의 내용이 다릅니다."}
                        saved = cur.execute("SELECT * FROM request_ledger WHERE ledger_id=?", (receipt[1],)).fetchone()
                        if saved is None or saved["status"] == "[삭제]":
                            return {"success": False, "code": "conflict",
                                    "error": "이미 처리된 등록 항목이 삭제되었습니다. 목록을 확인하세요."}
                        return {"success": True, "replayed": True, "db_saved": True, "sync_state": "check_status", "item": dict(saved),
                                "message": "이미 저장된 등록 요청입니다.",
                                "excel_message": "기존 등록을 확인했습니다. 엑셀 반영 상태는 동기화 배너에서 확인하세요."}
                ledger_id = requested_id or next_ledger_id(cur, year)
                item = {
                    "ledger_id": ledger_id, "year": year,
                    "seq_no": _clean(data.get('seq_no')), "party": _clean(data.get('party')),
                    "requester": mask_pii(requester), "aide": mask_pii(_clean(data.get('aide'))),
                    "title": mask_pii(title),
                    "details": mask_pii(_clean(data.get('details'))),
                    "request_date": dates["request_date"], "deadline": dates["deadline"],
                    "submit_date": dates["submit_date"],
                    "department": _clean(data.get('department')) or default_dept,
                    "status": _clean(data.get('status')) or "작성중", "note": mask_pii(_clean(data.get('note'))),
                    "request_type": _clean(data.get('request_type')) if data.get('request_type') is not None else "시스템",
                    "linked_doc_id": _clean(data.get('linked_doc_id')), "created_at": now_str, "updated_at": now_str,
                }
                auto_link_ledger_to_doc(conn, item)
                cur.execute("""
                INSERT INTO request_ledger (
                    ledger_id, year, seq_no, party, requester, aide,
                    title, details, request_date, deadline, submit_date,
                    department, status, note, request_type, linked_doc_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["ledger_id"], item["year"], item["seq_no"], item["party"], item["requester"], item["aide"],
                item["title"], item["details"], item["request_date"], item["deadline"], item["submit_date"],
                item["department"], item["status"], item["note"], item["request_type"], item["linked_doc_id"],
                item["created_at"], item["updated_at"]
            ))
                try:
                    cur.execute(
                    "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot, changed_fields, actor) VALUES (?, ?, ?, ?, ?, ?)",
                    (item["ledger_id"], "INSERT", now_str, json.dumps(item, ensure_ascii=False),
                     "", _history_actor())
                )
                except Exception:
                    pass
                enqueue_excel(conn, item, "insert")
                if request_key:
                    conn.execute("INSERT INTO ledger_receipts VALUES (?, ?, ?)", (request_key, fingerprint, ledger_id))
                conn.commit()
                break
            except sqlite3.IntegrityError as e:
                conn.rollback()
                last_error = e
                if requested_id:
                    return {"success": False, "error": f"이미 사용 중인 관리번호입니다: {requested_id}"}
                continue
            except Exception as e:
                conn.rollback()
                return {"success": False, "error": f"DB 등록 실패: {str(e)}"}
            finally:
                conn.close()
        else:
            return {"success": False, "error": f"DB 등록 실패: {str(last_error or '관리번호 발급 충돌')}"}

        # Trigger background JSON update
        self.sync_json_file(async_mode=True)
        self._bump_live_version()

        # Trigger Master Excel synchronization
        excel_res = {}
        try:
            from services.excel_sync_service import ExcelSyncService
            sync_svc = ExcelSyncService.get_instance(self.base_dir)
            excel_res = sync_svc.sync_item_to_excel(item, action="insert")
        except Exception as ex_err:
            excel_res = {"excel_synced": False, "pending": True, "message": "DB 저장 완료, 엑셀 자동 재시도 대기: " + str(ex_err)}

        print(f"✓ 신규 요구자료 DB 등록 성공: {item['ledger_id']} ({item['requester']} - {item['title']})")
        ret_msg = "성공적으로 등록되었습니다."
        if excel_res.get("message"):
            ret_msg += f" ({excel_res['message']})"
        return {
            "success": True,
            "message": ret_msg,
            "item": item,
            "db_saved": True,
            "sync_state": ("applied" if excel_res.get("excel_synced") else
                           "quarantined" if excel_res.get("quarantined") else
                           "pending" if excel_res.get("pending") else "db_only"),
            "excel_synced": excel_res.get("excel_synced", False),
            "excel_pending": excel_res.get("pending", False),
            "excel_message": excel_res.get("message", "")
        }

    _UPDATE_FIELDS = ("year", "seq_no", "party", "requester", "aide", "title", "details",
                      "request_date", "deadline", "submit_date", "department",
                      "status", "note", "request_type", "linked_doc_id")
    _DATE_FIELDS = ("request_date", "deadline", "submit_date")
    # 엑셀에 열이 없는 DB 전용 칸. 이것만 바뀐 수정은 엑셀 반영을 하지 않는다.
    _DB_ONLY_FIELDS = frozenset({"linked_doc_id"})

    @staticmethod
    def _apply_doc_link(cur, ledger_id, old_doc_id, new_doc_id):
        """대장↔문서 연결을 바꾼다. 오류 문구 또는 None.

        없는 문서로는 연결하지 않는다(끊어진 연결을 새로 만들지 않는다, 제안서 D3).
        예전 문서의 역참조는 이 대장을 가리킬 때만 지운다.
        """
        new_doc_id, old_doc_id = _clean(new_doc_id), _clean(old_doc_id)
        if new_doc_id:
            if not cur.execute("SELECT 1 FROM documents WHERE doc_id = ?", (new_doc_id,)).fetchone():
                return f"연결할 문서를 찾을 수 없습니다: {new_doc_id}"
        if old_doc_id and old_doc_id != new_doc_id:
            cur.execute(
                "UPDATE documents SET linked_ledger_id = '' WHERE doc_id = ? AND linked_ledger_id = ?",
                (old_doc_id, ledger_id),
            )
        if new_doc_id:
            cur.execute("UPDATE documents SET linked_ledger_id = ? WHERE doc_id = ?", (ledger_id, new_doc_id))
        return None

    @classmethod
    def _changed_update_fields(cls, existing, data):
        """요청 값 중 기존 행과 **실제로 다른** 필드만 정규화해 돌려준다. (clean_data, 오류)

        대시보드 편집 폼은 모든 필드를 보낸다. 요청에 들어 있다는 이유만으로 "바뀐 필드"로
        보면, 엑셀 반영·보류 큐 보호가 행 전체로 넓어져 보류 중 사용자가 엑셀에서 고친 다른
        칸을 옛 값으로 되돌리고(불변조건 4·14), 파서가 마스킹한 값을 엑셀 원본 칸에 다시
        쓴다. 비교는 DB에 저장되는 형태(마스킹·날짜 정규화 후)로 한다. (감사 7회차 ISSUE-001)
        """
        year = _clean(data.get("year")) or _clean(existing["year"])
        clean_data = {}
        for f in cls._UPDATE_FIELDS:
            if f not in data:
                continue
            val = _clean(data[f])
            if f in LEDGER_TEXT_FIELDS:
                val = mask_pii(val)
            current = _clean(existing[f])
            if val == current:
                continue
            if f in cls._DATE_FIELDS:
                coerced = coerce_ledger_date(val, year)
                if coerced is None:
                    return {}, f"날짜는 YYYY-MM-DD 형식이어야 합니다: {f} ({val})"
                val = coerced
                if val == current:
                    continue
            if f == "status" and not val and current:
                # 편집 폼 드롭다운에 없는 값(`완료`·`업무설명` 등)이 빈 값으로 넘어오던 경로.
                # 빈 상태는 엑셀 '제출' 칸을 지우고 다음 동기화에서 `미제출`로 바뀐다.
                # (감사 7회차 ISSUE-002)
                return {}, "진행상태는 비워둘 수 없습니다."
            clean_data[f] = val
        return clean_data, None

    def update_ledger_item(self, ledger_id, data):
        blocked = self._reject_if_pipeline_running()
        if blocked:
            return blocked
        if not isinstance(data, dict):
            return {"success": False, "error": "요청 본문은 JSON 객체여야 합니다."}
        self._guard_db_write()
        self._ensure_schema()
        if 'title' in data and not _clean(data['title']):
            return {"success": False, "error": "요구자료명(제목)은 비워둘 수 없습니다."}
        if 'requester' in data and not _clean(data['requester']):
            return {"success": False, "error": "요구자(의원실)는 비워둘 수 없습니다."}
        if 'year' in data and not re.fullmatch(r'\d{4}', _clean(data['year'])):
            return {"success": False, "error": "연도는 4자리 숫자여야 합니다."}
        # 날짜 형식은 기존 값과 비교해 **바뀐 필드만** 아래 트랜잭션 안에서 검사한다. 편집 폼은
        # 모든 필드를 다시 보내므로, 엑셀에서 들어온 `2026.9.10` 같은 값을 여기서 거절하면
        # 사용자가 날짜를 건드리지 않았는데도 상태·비고 수정까지 막힌다. (감사 7회차 ISSUE-003)
        data = dict(data)

        conn = self.get_conn()
        cur = conn.cursor()
        now_str = _now_str()

        try:
            # 확인과 갱신 사이에 다른 쓰기가 끼지 않게 쓰기 트랜잭션으로 묶는다.
            self._begin_immediate(cur)
            cur.execute("SELECT * FROM request_ledger WHERE ledger_id = ? AND COALESCE(status, '') != '[삭제]'", (ledger_id,))
            existing = cur.fetchone()
            if not existing:
                conn.rollback()
                return {"success": False, "code": "not_found", "error": "해당 항목을 찾을 수 없습니다."}

            # 낙관적 잠금. 화면이 읽어 간 시점의 updated_at을 보내면, 그사이 다른 사용자나 엑셀
            # 동기화가 고친 항목을 조용히 덮어쓰지 않는다. 보내지 않는 기존 호출자는 예전처럼 동작한다.
            # (감사 R4 4.1-1)
            if "expected_updated_at" in data:
                expected = _clean(data.get("expected_updated_at"))
                current = _clean(existing["updated_at"])
                if expected != current:
                    conn.rollback()
                    return {
                        "success": False,
                        "code": "conflict",
                        "conflict": True,
                        "error": "다른 사용자 또는 엑셀 동기화가 먼저 이 항목을 수정했습니다. 최신 내용을 다시 불러온 뒤 수정하세요.",
                        "current": dict(existing),
                    }

            # Auto-link if linked_doc_id not set in data or existing
            if not data.get('linked_doc_id') and not existing['linked_doc_id']:
                temp_item = dict(existing)
                temp_item.update(data)
                auto_link_ledger_to_doc(conn, temp_item)
                if temp_item.get('linked_doc_id'):
                    data['linked_doc_id'] = temp_item['linked_doc_id']

            clean_data, field_error = self._changed_update_fields(existing, data)
            if not field_error and "linked_doc_id" in clean_data:
                # 답변서 수동 연결·변경·해제. 문서 쪽 역참조도 같은 트랜잭션에서 맞춘다.
                field_error = self._apply_doc_link(cur, ledger_id, existing["linked_doc_id"],
                                                   clean_data["linked_doc_id"])
            if field_error:
                conn.rollback()
                return {"success": False, "code": "invalid", "error": field_error}
            if not clean_data:
                # 실제로 바뀐 칸이 없다. DB·이력·엑셀을 건드리지 않는다. 편집 폼을 그대로
                # 저장해도 엑셀 행이 다시 쓰이지 않아야 사용자의 수기 수정이 보존된다.
                conn.rollback()
                return {
                    "success": True,
                    "unchanged": True,
                    "message": "변경된 내용이 없습니다.",
                    "updated_at": existing["updated_at"],
                    "excel_synced": False,
                    "excel_pending": False,
                    "excel_message": "",
                }

            set_clauses = [f"{f} = ?" for f in clean_data]
            params = list(clean_data.values())
            set_clauses.append("updated_at = ?")
            params.append(now_str)
            params.append(ledger_id)

            # 어느 칸이 바뀌었는지 이력에 남긴다(제안서 D7). 스냅숏 JSON 한 덩어리만으로는
            # "이 항목의 마감일을 누가 언제 바꿨나"를 SQL로 물어볼 수 없었다.
            changed_field_names = list(clean_data.keys()) + ["updated_at"]

            # Record history snapshot
            try:
                cur.execute(
                    "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot, changed_fields, actor) VALUES (?, ?, ?, ?, ?, ?)",
                    (ledger_id, "UPDATE", now_str, json.dumps(dict(existing), ensure_ascii=False),
                     ",".join(sorted(changed_field_names)), _history_actor())
                )
            except Exception as e:
                # 이력 기록 실패를 삼키면 바뀐 건 있는데 흔적은 없음이 된다.
                # 본문 UPDATE는 계속하되 실패를 알린다. (감사 R6 정리)
                print(f"[대장 이력 경고] ledger_history 기록 실패: {e}")

            cur.execute(f"UPDATE request_ledger SET {', '.join(set_clauses)} WHERE ledger_id = ?", params)
            updated_item = dict(existing)
            updated_item.update(clean_data)
            updated_item["_changed_fields"] = list(clean_data)
            updated_item["_expected"] = row_fingerprint(existing)
            if not set(clean_data) <= self._DB_ONLY_FIELDS:
                enqueue_excel(conn, updated_item, "update")
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return {"success": False, "error": f"DB 수정 실패: {str(e)}"}
        finally:
            conn.close()

        self.sync_json_file(async_mode=True)
        self._bump_live_version()

        # `linked_doc_id` 같은 DB 전용 칸만 바뀌었으면 엑셀을 열지 않는다. 엑셀에 그 열이
        # 없으므로 쓸 것도 없고, 워크북을 통째로 다시 저장하거나 보류 큐에 빈 작업을 쌓을
        # 이유가 없다(불변조건 41 — 연결은 DB 전용 파생값).
        if set(clean_data) <= self._DB_ONLY_FIELDS:
            return {
                "success": True,
                "message": "성공적으로 수정되었습니다.",
                "updated_at": now_str,
                "changes": clean_data,
                "excel_synced": False,
                "excel_pending": False,
                "excel_message": "",
            }

        # Trigger Master Excel update
        excel_res = {}
        try:
            from services.excel_sync_service import ExcelSyncService
            sync_svc = ExcelSyncService.get_instance(self.base_dir)
            excel_res = sync_svc.sync_item_to_excel(updated_item, action="update")
        except Exception as ex_err:
            excel_res = {"excel_synced": False, "pending": True, "message": "DB 저장 완료, 엑셀 자동 재시도 대기: " + str(ex_err)}

        print(f"✓ 요구자료 수정 성공: {ledger_id}")
        ret_msg = "성공적으로 수정되었습니다."
        if excel_res.get("message"):
            ret_msg += f" ({excel_res['message']})"
        return {
            "success": True,
            "message": ret_msg,
            # 화면은 이 값을 다음 수정의 expected_updated_at으로 쓴다.
            "updated_at": now_str,
            # 실제로 저장한 값(날짜 정규화·마스킹 후). 화면이 입력값 대신 이 값을 반영한다.
            "changes": clean_data,
            "db_saved": True,
            "sync_state": ("applied" if excel_res.get("excel_synced") else
                           "quarantined" if excel_res.get("quarantined") else
                           "pending" if excel_res.get("pending") else "db_only"),
            "excel_synced": excel_res.get("excel_synced", False),
            "excel_pending": excel_res.get("pending", False),
            "excel_message": excel_res.get("message", "")
        }

    def delete_ledger_item(self, ledger_id):
        blocked = self._reject_if_pipeline_running()
        if blocked:
            return blocked
        self._guard_db_write()
        self._ensure_schema()
        conn = self.get_conn()
        cur = conn.cursor()
        now_str = _now_str()
        deleted_item = {"ledger_id": ledger_id}

        try:
            self._begin_immediate(cur)
            cur.execute("SELECT * FROM request_ledger WHERE ledger_id = ? AND COALESCE(status, '') != '[삭제]'", (ledger_id,))
            existing = cur.fetchone()
            if not existing:
                return {"success": False, "code": "not_found", "error": "해당 항목을 찾을 수 없습니다."}
            deleted_item = dict(existing)

            try:
                cur.execute(
                    "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot, changed_fields, actor) VALUES (?, ?, ?, ?, ?, ?)",
                    (ledger_id, "DELETE", now_str, json.dumps(deleted_item, ensure_ascii=False),
                     "", _history_actor())
                )
            except Exception:
                pass

            # Retain a tombstone so Excel's soft-delete row cannot resurrect the
            # record during startup/watcher/manual synchronization.
            cur.execute("UPDATE request_ledger SET status = ?, updated_at = ? WHERE ledger_id = ?", ("[삭제]", now_str, ledger_id))
            delete_op = dict(deleted_item)
            delete_op["_expected"] = row_fingerprint(deleted_item)
            enqueue_excel(conn, delete_op, "delete")
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {"success": False, "error": f"DB 삭제 실패: {str(e)}"}
        finally:
            conn.close()

        self.sync_json_file(async_mode=True)
        self._bump_live_version()

        # Trigger Master Excel delete
        excel_res = {}
        try:
            from services.excel_sync_service import ExcelSyncService
            sync_svc = ExcelSyncService.get_instance(self.base_dir)
            excel_res = sync_svc.sync_item_to_excel(delete_op, action="delete")
        except Exception as ex_err:
            excel_res = {"excel_synced": False, "pending": True, "message": "DB 저장 완료, 엑셀 자동 재시도 대기: " + str(ex_err)}

        print(f"✓ 요구자료 삭제 성공: {ledger_id}")
        ret_msg = "삭제되었습니다."
        if excel_res.get("message"):
            ret_msg += f" ({excel_res['message']})"
        return {
            "success": True,
            "message": ret_msg,
            "db_saved": True,
            "sync_state": ("applied" if excel_res.get("excel_synced") else
                           "quarantined" if excel_res.get("quarantined") else
                           "pending" if excel_res.get("pending") else "db_only"),
            "excel_synced": excel_res.get("excel_synced", False),
            "excel_pending": excel_res.get("pending", False),
            "excel_message": excel_res.get("message", "")
        }

    def get_ledger_history(self, ledger_id=None, limit=100):
        self._ensure_schema()
        try:
            limit = max(1, min(int(limit), 1000))
        except (ValueError, TypeError):
            limit = 100
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            if ledger_id:
                cur.execute(
                    "SELECT * FROM ledger_history WHERE ledger_id = ? ORDER BY history_id DESC LIMIT ?",
                    (ledger_id, limit)
                )
            else:
                cur.execute(
                    "SELECT * FROM ledger_history ORDER BY history_id DESC LIMIT ?",
                    (limit,)
                )
            rows = [dict(r) for r in cur.fetchall()]
            result = {"success": True, "history": rows}
            if ledger_id:
                # 화면용 해석본: 칸마다 이전 값 → 이후 값. 원본 행(`history`)은 그대로 둔다.
                current = cur.execute(
                    "SELECT * FROM request_ledger WHERE ledger_id = ?", (ledger_id,)
                ).fetchone()
                result["timeline"] = build_ledger_timeline(rows, dict(current) if current else None, limit=limit)
            return result
        finally:
            conn.close()

    def restore_ledger_item(self, ledger_id):
        """웹에서 삭제한 항목을 되살린다(삭제 직후 '되돌리기').

        DB: 삭제 직전 스냅숏의 진행상태·비고로 되돌리고 `RESTORE` 이력을 남긴다.
        엑셀: 웹 삭제는 행을 지우지 않고 진행상태 `[삭제]`·비고 `(웹 삭제됨)`만 적는다. 되살릴 때도
        같은 행의 **그 두 칸만** 수정 작업으로 되돌린다(불변조건 4·45 — 바뀐 칸만 쓴다). 엑셀이 열려
        있으면 보류 큐에 쌓이고, 삭제가 아직 큐에 있으면 삭제 → 복원 순서로 재생된다.
        """
        blocked = self._reject_if_pipeline_running()
        if blocked:
            return blocked
        self._guard_db_write()
        self._ensure_schema()
        conn = self.get_conn()
        cur = conn.cursor()
        now_str = _now_str()
        try:
            self._begin_immediate(cur)
            row = cur.execute("SELECT * FROM request_ledger WHERE ledger_id = ?", (ledger_id,)).fetchone()
            if not row:
                conn.rollback()
                return {"success": False, "code": "not_found", "error": "해당 항목을 찾을 수 없습니다."}
            if _clean(row["status"]) != "[삭제]":
                conn.rollback()
                return {"success": False, "code": "invalid", "error": "삭제된 항목이 아니라 되살릴 것이 없습니다."}
            hist = cur.execute(
                "SELECT snapshot FROM ledger_history WHERE ledger_id = ? AND action = 'DELETE' "
                "ORDER BY history_id DESC LIMIT 1", (ledger_id,)
            ).fetchone()
            try:
                before = json.loads(hist["snapshot"]) if hist else {}
            except (TypeError, ValueError):
                before = {}
            if not isinstance(before, dict) or _clean(before.get("status")) in ("", "[삭제]"):
                conn.rollback()
                return {"success": False, "code": "invalid",
                        "error": "삭제 직전 상태 기록이 없어 되살릴 수 없습니다. 엑셀에서 직접 고쳐 주세요."}
            tombstone = dict(row)
            restored = dict(row)
            restored["status"] = _clean(before.get("status"))
            restored["note"] = _clean(before.get("note"))
            restored["updated_at"] = now_str
            cur.execute(
                "UPDATE request_ledger SET status = ?, note = ?, updated_at = ? WHERE ledger_id = ?",
                (restored["status"], restored["note"], now_str, ledger_id),
            )
            cur.execute(
                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot, changed_fields, actor) VALUES (?, ?, ?, ?, ?, ?)",
                (ledger_id, "RESTORE", now_str, json.dumps(restored, ensure_ascii=False),
                 "note,status,updated_at", _history_actor()),
            )
            op = dict(restored)
            op["_changed_fields"] = ["status", "note"]
            op["_expected"] = row_fingerprint(tombstone)
            enqueue_excel(conn, op, "update")
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return {"success": False, "error": f"DB 복원 실패: {str(e)}"}
        finally:
            conn.close()

        self.sync_json_file(async_mode=True)
        self._bump_live_version()

        excel_res = {}
        try:
            from services.excel_sync_service import ExcelSyncService
            sync_svc = ExcelSyncService.get_instance(self.base_dir)
            excel_res = sync_svc.sync_item_to_excel(op, action="update")
        except Exception as ex_err:
            excel_res = {"excel_synced": False, "pending": True, "message": "DB 저장 완료, 엑셀 자동 재시도 대기: " + str(ex_err)}

        msg = "삭제를 취소하고 항목을 되살렸습니다."
        if excel_res.get("message"):
            msg += f" ({excel_res['message']})"
        return {
            "success": True,
            "message": msg,
            "item": restored,
            "updated_at": now_str,
            "db_saved": True,
            "sync_state": ("applied" if excel_res.get("excel_synced") else
                           "quarantined" if excel_res.get("quarantined") else
                           "pending" if excel_res.get("pending") else "db_only"),
            "excel_synced": excel_res.get("excel_synced", False),
            "excel_pending": excel_res.get("pending", False),
            "excel_message": excel_res.get("message", ""),
        }
