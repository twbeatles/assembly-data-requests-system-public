# -*- coding: utf-8 -*-
"""읽기 전용 Facade. CLI와 MCP가 공유하는 유일한 진입점이다.

원칙:
- 조회는 읽기 전용 연결·읽기 전용 서비스로만 실행한다. DB 쓰기·엑셀 쓰기·JSON 쓰기·
  파일 이동·스키마 준비·마이그레이션·검색 로그 쓰기를 하지 않는다.
- DB가 없거나 스키마가 불완전·구버전·미래 버전이면 파일을 만들거나 고치지 않고
  `dataset_unavailable`로 실패한다.
- v1의 reader/analyst/operator 프로파일은 모두 같은 읽기 전용 범위다. 프로파일별
  차등은 승인 체계가 확정된 뒤에 둔다. 쓰기·삭제·재빌드 진입점은 노출하지 않는다.
"""

import contextlib
import datetime
import hashlib
import sqlite3
import time
from pathlib import Path

from . import policy, search as search_adapter
from .errors import DataReqError


def _utcnow_iso():
    return (datetime.datetime.now(datetime.timezone.utc)
            .astimezone().isoformat(timespec="seconds"))


class AgentFacade:
    def __init__(self, base_dir, allowed_roots=None, profile="reader",
                 immutable=False):
        self.base_dir = Path(base_dir)
        if profile not in policy.PROFILES:
            raise DataReqError("invalid_input", "알 수 없는 권한 프로파일입니다.")
        self.profile = profile
        self.immutable = bool(immutable)
        self.allowed_roots = ([Path(p) for p in allowed_roots]
                              if allowed_roots
                              else policy.default_allowed_roots(self.base_dir))

    # -- 내부 도우미 ------------------------------------------------------

    def _service(self):
        from services.ledger_service import LedgerService
        db_path = self.base_dir / "data_requests.db"
        json_path = self.base_dir / "data_requests.json"
        # 조회 전용 서비스로만 실행한다. 스키마 준비·마이그레이션·검색 로그 쓰기를
        # 하지 않으며, 없는 DB를 만들지도 않는다. immutable 선택도 그대로 전달한다.
        # (감사 ISSUE-002)
        return LedgerService(db_path=db_path, json_path=json_path,
                             base_dir=self.base_dir, read_only=True,
                             immutable=self.immutable)

    def _db_path(self):
        return self.base_dir / "data_requests.db"

    def _require_db(self):
        db_path = self._db_path()
        if not db_path.exists():
            raise DataReqError("dataset_unavailable", "SQLite DB 파일이 없습니다.")
        return db_path

    def _require_schema(self):
        """필요 테이블·지원 스키마 버전이 아니면 서비스를 부르지 않는다.

        조회가 DB를 쓰거나(빈 DB 생성·DDL·마이그레이션) 구/미래 스키마를
        자동 준비하는 부작용을 막는다. 모든 agent 조회가 서비스 호출보다 먼저
        이 검사를 통과해야 한다. (감사 ISSUE-002)
        """
        self._require_db()
        conn = self._readonly_conn()
        try:
            cur = conn.cursor()
            try:
                version = int(cur.execute("PRAGMA user_version").fetchone()[0])
            except Exception:
                raise DataReqError("dataset_unavailable", "SQLite 스키마가 완전하지 않습니다.")
            from db.migrations import SCHEMA_VERSION
            if version > SCHEMA_VERSION:
                raise DataReqError(
                    "dataset_unavailable",
                    "DB 버전 %s은 지원 버전 %s보다 높습니다. 최신 프로그램으로 실행하세요."
                    % (version, SCHEMA_VERSION))
            if version < SCHEMA_VERSION:
                raise DataReqError(
                    "dataset_unavailable",
                    "DB 스키마가 오래되었습니다 (버전 %s, 지원 %s). "
                    "00번 파이프라인으로 DB를 구축한 뒤 다시 시도하세요."
                    % (version, SCHEMA_VERSION))
            try:
                names = {r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            except Exception:
                raise DataReqError("dataset_unavailable", "SQLite 스키마가 완전하지 않습니다.")
        finally:
            conn.close()
        if not {"documents", "qa_items", "request_ledger"}.issubset(names):
            raise DataReqError("dataset_unavailable", "SQLite 스키마가 완전하지 않습니다.")

    @contextlib.contextmanager
    def _no_search_logging(self, service):
        """검색 로그·정리 쓰기를 호출 동안만 끈다. 기존 메서드는 그대로 둔다."""
        original_log = service._log_search
        original_prune = service._maybe_prune_search_log
        service._log_search = lambda *a, **k: None
        service._maybe_prune_search_log = lambda *a, **k: None
        try:
            yield
        finally:
            service._log_search = original_log
            service._maybe_prune_search_log = original_prune

    def _readonly_conn(self):
        from extractors.ledger.workbook import connect_readonly
        db_path = self._require_db()
        conn = connect_readonly(db_path, immutable=self._effective_immutable())
        conn.row_factory = sqlite3.Row
        return conn

    def dataset_version(self):
        """DB 지문. 없으면 'unavailable'.

        건수·파일 크기·초 단위 mtime만 보던 예전 지문은 WAL에 커밋된 같은 행 수정을
        구분하지 못했다. 새 지문은 읽기 전용 연결(mode=ro, WAL 내용 포함)로 읽은
        내용 집계(이력 ID·갱신 시각·행 해시)와 파일 식별자를 함께 해시한다.
        지문을 얻기 위해 checkpoint 같은 쓰기는 하지 않는다. (감사 ISSUE-003)
        """
        db_path = self._db_path()
        if not db_path.exists():
            return "unavailable"
        try:
            conn = self._readonly_conn()
            try:
                cur = conn.cursor()
                version = cur.execute("PRAGMA user_version").fetchone()[0]
                docs = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                qa = cur.execute("SELECT COUNT(*) FROM qa_items").fetchone()[0]
                ledger = cur.execute(
                    "SELECT COUNT(*) FROM request_ledger "
                    "WHERE COALESCE(status, '') != '[삭제]'").fetchone()[0]
                tombstones = cur.execute(
                    "SELECT COUNT(*) FROM request_ledger "
                    "WHERE status = '[삭제]'").fetchone()[0]
                docs_seq = cur.execute("SELECT MAX(rowid) FROM documents").fetchone()[0]
                qa_seq = cur.execute("SELECT MAX(rowid) FROM qa_items").fetchone()[0]
                ledger_seq = cur.execute(
                    "SELECT MAX(rowid) FROM request_ledger").fetchone()[0]
                ledger_updated = cur.execute(
                    "SELECT MAX(updated_at) FROM request_ledger").fetchone()[0]
                try:
                    hist_seq = cur.execute(
                        "SELECT MAX(history_id) FROM ledger_history").fetchone()[0]
                except Exception:
                    hist_seq = None
                ledger_hash = self._ledger_content_hash(cur)
            finally:
                conn.close()
            # 파일 식별자는 inode·장치만 쓴다. 크기·mtime은 checkpoint만으로 바뀌어
            # 내용 불변에도 지문이 흔들리고, 재빌드(os.replace)는 inode가 바뀌어 구분된다.
            stat = db_path.stat()
            canonical = "|".join(str(part) for part in (
                version, docs, qa, ledger, tombstones,
                docs_seq, qa_seq, ledger_seq, ledger_updated, hist_seq,
                ledger_hash, stat.st_ino, stat.st_dev))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
            return "v%s-docs%s-qa%s-ledger%s-tomb%s-rev%s" % (
                version, docs, qa, ledger, tombstones, digest)
        except Exception:
            return "unavailable"

    @staticmethod
    def _ledger_content_hash(cur):
        """대장 전 행의 내용 해시. 건수를 바꾸지 않는 UPDATE도 구분한다.

        대장 표는 수백~수천 행 규모라 전 행 해시가 부담되지 않는다. 문서/Q&A 전문은
        크기가 커서 건수·rowid·파일 식별자로만 식별한다 (재빌드 시 바뀐다).
        """
        digest = hashlib.sha256()
        for row in cur.execute("SELECT * FROM request_ledger ORDER BY ledger_id"):
            digest.update(repr(tuple(row)).encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    # -- 상태·진단 --------------------------------------------------------

    def status(self):
        started = time.time()
        db_path = self._db_path()
        if not db_path.exists():
            return {"available": False, "db_exists": False,
                    "dataset_version": "unavailable"}
        try:
            self._require_schema()
        except DataReqError:
            return {"available": False, "db_exists": True,
                    "dataset_version": "unavailable",
                    "reason": "schema_incomplete"}
        service = self._service()
        with self._no_search_logging(service):
            stats = service.get_stats()
        return {
            "available": bool(stats.get("success")),
            "db_exists": True,
            "documents": stats.get("documents", 0),
            "qa_items": stats.get("qa_items", 0),
            "request_ledger": stats.get("request_ledger", 0),
            "dataset_version": self.dataset_version(),
            "profile": self.profile,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    def _effective_immutable(self):
        """-wal이 없을 때만 자동으로 immutable을 켠다.

        사이드카 생성 없이 읽으면서도 stale 우려가 없다. -wal이 있으면 mode=ro로
        열어 최신 커밋까지 본다. (감사 ISSUE-002/003)
        """
        if self.immutable:
            return True
        try:
            return not self._db_path().with_name(
                self._db_path().name + "-wal").exists()
        except OSError:
            return False

    def doctor(self, deep=False):
        from db.health import check_database
        db_path = self._require_db()
        # 무거운 검사는 명시적 opt-in일 때만 돈다.
        return check_database(db_path, deep=bool(deep),
                              immutable=self._effective_immutable())

    # -- 검색·조회 --------------------------------------------------------

    def search(self, query, category="all", year=None, limit=None, cursor=None):
        started = time.time()
        self._require_schema()
        limit = policy.clamp_limit(limit)
        service = self._service()
        search_type = {"all": "all", "qa": "qa", "doc": "docs", "docs": "docs",
                       "ledger": "ledger"}.get(category, "all")
        with self._no_search_logging(service):
            if search_type == "ledger":
                rows = service.query_ledger(year=year, kw=query,
                                            with_snippet=True)
                kinds = ["ledger"] * len(rows)
            elif search_type == "docs":
                rows = service.query_documents(year=year, kw=query, slim=True,
                                               with_snippet=True)
                kinds = ["doc"] * len(rows)
            elif search_type == "qa":
                rows = service.query_qa_items(year=year, kw=query, slim=True,
                                              with_snippet=True)
                kinds = ["qa"] * len(rows)
            else:
                result = service.search_all(kw=query, year=year, slim=True,
                                            with_snippet=True)
                rows = (result.get("ledger", []) + result.get("documents", [])
                        + result.get("qa_items", []))
                kinds = (["ledger"] * len(result.get("ledger", []))
                         + ["doc"] * len(result.get("documents", []))
                         + ["qa"] * len(result.get("qa_items", [])))
        items = [self._shape(kind, row, query) for kind, row in zip(kinds, rows)]
        page, next_cursor = search_adapter.paginate(items, limit, cursor)
        return {
            "schema": "datareq-search/v1",
            "query": query,
            "dataset_version": self.dataset_version(),
            "items": page,
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    def _shape(self, kind, row, query):
        """목록용 발췌 항목. 목록 인용은 미검증扱い, 해시 검증은 단건 조회에서 한다."""
        excerpt = policy.mask_text(str(row.get("snippet") or "")[:policy.MAX_SNIPPET_CHARS])
        stable = {"qa": row.get("qa_id"), "doc": row.get("doc_id"),
                  "ledger": row.get("ledger_id")}.get(kind, "")
        title = str(row.get("question_title") or row.get("title") or "")
        doc_ref = row.get("doc_id") if kind == "qa" else None
        if kind == "doc":
            doc_ref = row.get("doc_id")
        if kind == "ledger":
            doc_ref = row.get("linked_doc_id") or None
        source_id = "%s:%s" % (kind, stable)
        return {
            "kind": kind,
            "source_id": source_id,
            "document_id": doc_ref,
            "title": title,
            "year": str(row.get("year") or ""),
            "snippet": excerpt,
            "snippet_range": None,
            "location_quality": "unknown",
            "retrieval_method": "fts_or_fallback",
            "score": row.get("rank"),
            "score_note": "relative rank (lower is better for FTS, higher for fallback)",
            "citation": {"source_id": source_id, "status": "unverified"},
        }

    def _single_row(self, table, key, value, body_column):
        conn = self._readonly_conn()
        try:
            cur = conn.cursor()
            cols = [r[1] for r in cur.execute("PRAGMA table_info(%s)" % table)]
            if key not in cols or body_column not in cols:
                return None
            row = cur.execute(
                "SELECT * FROM %s WHERE %s = ?" % (table, key), (value,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_document(self, doc_id, max_chars=None, offset=None):
        max_chars = policy.clamp_chars(max_chars, policy.MAX_EXCERPT_CHARS,
                                       policy.MAX_EXCERPT_CHARS)
        try:
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise DataReqError("invalid_input", "offset은 0 이상의 정수여야 합니다.")
        row = self._single_row("documents", "doc_id", doc_id, "full_markdown")
        if row is None:
            # 테이블·행이 없으면 데이터셋 문제와 ID 문제를 구분한다.
            # 스키마 검사가 먼저다. 없으면 만들지 않고 실패한다. (감사 ISSUE-002)
            self._require_schema()
            raise DataReqError("invalid_input", "해당 문서 ID가 없습니다.")
        body = str(row.get("full_markdown") or "")
        excerpt = body[start:start + max_chars]
        next_offset = start + len(excerpt) if start + len(excerpt) < len(body) else None
        return {
            "doc_id": doc_id,
            "title": str(row.get("title") or ""),
            "year": str(row.get("year") or ""),
            "total_chars": len(body),
            "excerpt": policy.mask_text(excerpt),
            "offset": start,
            "next_offset": next_offset,
            "truncated": next_offset is not None,
            "content_sha256": search_adapter.content_hash(body),
        }

    def get_qa(self, qa_id, max_chars=None, offset=None):
        max_chars = policy.clamp_chars(max_chars, policy.MAX_EXCERPT_CHARS,
                                       policy.MAX_EXCERPT_CHARS)
        try:
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise DataReqError("invalid_input", "offset은 0 이상의 정수여야 합니다.")
        row = self._single_row("qa_items", "qa_id", qa_id, "answer_markdown")
        if row is None:
            # 스키마 검사가 먼저다. 없으면 만들지 않고 실패한다. (감사 ISSUE-002)
            self._require_schema()
            raise DataReqError("invalid_input", "해당 Q&A ID가 없습니다.")
        body = str(row.get("answer_markdown") or "")
        excerpt = body[start:start + max_chars]
        next_offset = start + len(excerpt) if start + len(excerpt) < len(body) else None
        return {
            "qa_id": qa_id,
            "doc_id": str(row.get("doc_id") or "") or None,
            "question_title": str(row.get("question_title") or ""),
            "year": str(row.get("year") or ""),
            "total_chars": len(body),
            "excerpt": policy.mask_text(excerpt),
            "offset": start,
            "next_offset": next_offset,
            "truncated": next_offset is not None,
            "content_sha256": search_adapter.content_hash(body),
        }

    # -- 대장 조회 --------------------------------------------------------

    def ledger_list(self, year=None, status=None, due=None, limit=None, cursor=None):
        self._require_schema()
        limit = policy.clamp_limit(limit)
        service = self._service()
        with self._no_search_logging(service):
            rows = service.query_ledger(year=year, kw="", status=status, due=due)
        items = [policy.mask_record(r) for r in rows]
        page, next_cursor = search_adapter.paginate(items, limit, cursor)
        return {"items": page, "next_cursor": next_cursor,
                "truncated": next_cursor is not None, "total": len(items)}

    def ledger_get(self, ledger_id):
        # 스키마 확인이 서비스 호출보다 먼저다. 없으면 만들지 않고 실패한다. (감사 ISSUE-002)
        self._require_schema()
        service = self._service()
        with self._no_search_logging(service):
            row = service.get_ledger_item(ledger_id)
        if row is None:
            raise DataReqError("invalid_input", "해당 대장 ID가 없습니다.")
        return policy.mask_record(row)

    def ledger_summary(self, year=None):
        self._require_schema()
        service = self._service()
        with self._no_search_logging(service):
            return service.ledger_summary(year=year)

    def ledger_history(self, ledger_id, limit=None):
        try:
            count = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            raise DataReqError("invalid_input", "limit은 정수여야 합니다.")
        # 스키마 확인이 서비스 호출보다 먼저다. 없으면 만들지 않고 실패한다. (감사 ISSUE-002)
        self._require_schema()
        service = self._service()
        with self._no_search_logging(service):
            result = service.get_ledger_history(ledger_id=ledger_id, limit=count)
        history = result.get("history", [])
        masked = []
        for entry in history:
            item = dict(entry)
            snapshot = item.get("snapshot")
            item["snapshot"] = self._mask_snapshot(snapshot)
            masked.append(item)
        result["history"] = masked
        return result

    @staticmethod
    def _mask_snapshot(snapshot):
        import json as _json
        if not snapshot:
            return snapshot
        try:
            data = _json.loads(snapshot) if isinstance(snapshot, str) else dict(snapshot)
        except (ValueError, TypeError):
            return policy.mask_text(str(snapshot))
        for field in ("contact_person", "aide"):
            if data.get(field):
                data[field] = policy.mask_text(data[field])
        try:
            return _json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return policy.mask_text(str(snapshot))

    # -- 정합성·파이프라인 계획 --------------------------------------------

    def reconcile(self):
        """SQLite↔JSON↔마스터 엑셀 건수 대조. 읽기 전용, 아무것도 고치지 않는다."""
        import json as _json
        report = {"db": {}, "json": {}, "excel": {}, "mismatch": False}
        db_path = self._db_path()
        if db_path.exists():
            conn = self._readonly_conn()
            try:
                cur = conn.cursor()
                required = {r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if {"documents", "qa_items", "request_ledger"}.issubset(required):
                    report["db"] = {
                        "available": True,
                        "documents": cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                        "qa_items": cur.execute("SELECT COUNT(*) FROM qa_items").fetchone()[0],
                        "request_ledger": cur.execute(
                            "SELECT COUNT(*) FROM request_ledger "
                            "WHERE COALESCE(status, '') != '[삭제]'").fetchone()[0],
                        "tombstones": cur.execute(
                            "SELECT COUNT(*) FROM request_ledger "
                            "WHERE status = '[삭제]'").fetchone()[0],
                    }
                else:
                    report["db"] = {"available": False, "error": "SQLite 스키마가 완전하지 않습니다."}
            finally:
                conn.close()
        else:
            report["db"] = {"available": False, "error": "SQLite DB 파일이 없습니다."}
        json_path = self.base_dir / "data_requests.json"
        if json_path.exists():
            try:
                data = _json.loads(json_path.read_text(encoding="utf-8"))
                report["json"] = {
                    "available": True,
                    "documents": len(data.get("documents", [])),
                    "qa_items": len(data.get("qa_items", [])),
                    "request_ledger": len(data.get("request_ledger", [])),
                }
            except (OSError, ValueError, TypeError) as exc:
                report["json"] = {"available": False, "error": "JSON 읽기 실패: %s" % exc}
        else:
            report["json"] = {"available": False, "error": "JSON 캐시 파일이 없습니다."}
        if report["db"].get("available") and report["json"].get("available"):
            report["mismatch"] = any(
                report["db"].get(k) != report["json"].get(k)
                for k in ("documents", "qa_items", "request_ledger"))
        return report

    def pipeline_plan(self, input_dir):
        """투입 폴더 사전 점검. 계획만 세우고 파일을 옮기거나 DB를 건드리지 않는다."""
        target = policy.resolve_allowed_root(input_dir, self.allowed_roots)
        if not target.exists() or not target.is_dir():
            raise DataReqError("invalid_input", "투입 폴더가 없습니다.")
        try:
            from pipeline_guard import PipelineGuard
            guard = PipelineGuard(self.base_dir)
            locked = guard.is_locked()
        except Exception:
            locked = False
        try:
            from pipeline.parse.year_detect import detect_target_year
        except Exception:
            detect_target_year = None
        files = []
        by_year = {}
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            year = None
            if detect_target_year is not None:
                try:
                    year = detect_target_year(path)
                except Exception:
                    year = None
            files.append({"name": path.name, "suffix": suffix,
                          "size_bytes": path.stat().st_size,
                          "year": str(year) if year else "미분류"})
            by_year[str(year) if year else "미분류"] = by_year.get(
                str(year) if year else "미분류", 0) + 1
            if len(files) >= 500:
                break
        return {
            "input": str(target),
            "file_count": len(files),
            "by_year": by_year,
            "files": files[:50],
            "files_truncated": len(files) > 50,
            "pipeline_locked": bool(locked),
            "note": "계획만 출력합니다. 파일 이동·DB 변경은 하지 않습니다.",
        }

    # -- 근거 패키지 ------------------------------------------------------

    def evidence_pack(self, question, selected_ids=None, max_sources=None):
        from . import evidence as evidence_mod
        return evidence_mod.build_pack(self, question, selected_ids, max_sources)
