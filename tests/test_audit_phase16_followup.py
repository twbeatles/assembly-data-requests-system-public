# -*- coding: utf-8 -*-
"""PROJECT_AUDIT.md 후속 조치 회귀 테스트 (ISSUE-001/002/003, GAP-001/002/003).

- ISSUE-001: 조회 경로가 운영 DB에 쓰지 않는다. 관측 로그는 전용 파일이다.
- ISSUE-002: 파이프라인 timeout·취소는 프로세스 트리째 끝낸다.
- ISSUE-003: JSON 교체 실패는 기존 파일을 유지하고 실패를 알린다.
- GAP-001: 파이프라인 취소 API.
- GAP-002: 원자 저장은 정본에 위임한다.
- 운영 데이터에 쓰지 않는다. 모든 서비스에 임시 base_dir를 준다.
"""

import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from db.database_manager import DatabaseManager
from pipeline_guard import pid_is_alive
from services.ledger_service import LedgerService
from services.sync_service import SyncService

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _wait_until(predicate, timeout=30.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class SearchLogSeparationTests(unittest.TestCase):
    """ISSUE-001: 조회는 운영 DB를 바꾸지 않는다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="followup_searchlog_")
        self.base = Path(self.tmp)
        self.db = self.base / "data_requests.db"
        conn = sqlite3.connect(str(self.db))
        try:
            DatabaseManager.ensure_schema(conn)
        finally:
            conn.close()
        self.svc = LedgerService(
            db_path=self.db,
            json_path=self.base / "data_requests.json",
            base_dir=self.base,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main_log_count(self):
        conn = sqlite3.connect(str(self.db))
        try:
            return conn.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
        finally:
            conn.close()

    def test_search_leaves_main_db_untouched(self):
        self.svc.search_all(kw="없는질의어xyz123")
        before_bytes = self.db.read_bytes()
        before_count = self._main_log_count()
        self.svc.search_all(kw="없는질의어xyz123")
        self.assertEqual(self.db.read_bytes(), before_bytes)
        self.assertEqual(self._main_log_count(), before_count)
        log_db = self.base / "data_requests_searchlog.db"
        self.assertTrue(log_db.exists())
        conn = sqlite3.connect(str(log_db))
        try:
            rows = conn.execute("SELECT query FROM search_log").fetchall()
        finally:
            conn.close()
        self.assertTrue(any("없는질의어xyz123" in r[0] for r in rows))

    def test_insights_reads_separate_log_db(self):
        self.svc.query_documents(kw="존재하지않는낱말xyz", slim=True)
        insights = self.svc.search_insights()
        self.assertTrue(insights["success"])
        self.assertTrue(
            any(r["query"] == "존재하지않는낱말xyz" for r in insights["zero_result"])
        )
        self.assertEqual(self._main_log_count(), 0)

    def test_legacy_rows_imported_once(self):
        now = "2099-01-01T00:00:00"
        conn = sqlite3.connect(str(self.db))
        try:
            conn.executemany(
                "INSERT INTO search_log (query, mode, result_count, elapsed_ms, "
                "used_fts, searched_at) VALUES (?, ?, ?, ?, ?, ?)",
                [("옛질의1", "docs", 0, 5, 0, now), ("옛질의2", "docs", 3, 7, 1, now)],
            )
            conn.commit()
        finally:
            conn.close()

        def log_count():
            c = sqlite3.connect(str(self.base / "data_requests_searchlog.db"))
            try:
                return c.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
            finally:
                c.close()

        self.svc.search_all(kw="새질의어abc")
        after_first = log_count()
        # 기존 2건 + 이번 검색 1회분. 검색 1회가 종류별(대장·문서·Q&A)로 여러 건을
        # 남겨도, 가져온 기존 행이 중복되지 않으면 된다.
        self.assertGreaterEqual(after_first, 3)
        self.svc.search_all(kw="새질의어abc")
        after_second = log_count()
        self.assertEqual(after_second - after_first, after_first - 2)

    def test_readonly_service_writes_nothing(self):
        ro = LedgerService(
            db_path=self.db,
            json_path=self.base / "data_requests.json",
            base_dir=self.base,
            read_only=True,
        )
        ro._log_search("docs", "읽기전용질의", 0, 1, False)
        self.assertFalse(ro.log_db_path.exists())

    def test_failed_append_increments_dropped_counter(self):
        from db import search_log as search_log_store

        before = search_log_store.dropped_count()
        ok = search_log_store.append(
            self.base,
            query="q",
            mode="docs",
            result_count=0,
            elapsed_ms=1,
            used_fts=False,
        )
        self.assertFalse(ok)
        self.assertEqual(search_log_store.dropped_count(), before + 1)


class SyncTreeKillTests(unittest.TestCase):
    """ISSUE-002: timeout은 파이프라인 트리째 끝낸다."""

    FAKE_PARENT = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        'marker = Path(r"{marker}")\n'
        "g = subprocess.Popen(\n"
        '    [sys.executable, "-c", "import time; time.sleep(60)"],\n'
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "marker.write_text(str(g.pid))\n"
        "time.sleep(60)\n"
    )

    def _scripts_dir_with_sleeper(self):
        tmp = Path(tempfile.mkdtemp(prefix="followup_sync_"))
        marker = tmp / "grandchild.pid"
        (tmp / "add_documents_smart.py").write_text(
            self.FAKE_PARENT.format(marker=str(marker)), encoding="utf-8"
        )
        return tmp, marker

    def _grandchild_pid(self, marker):
        try:
            return int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def test_timeout_kills_grandchild_tree(self):
        scripts_dir, marker = self._scripts_dir_with_sleeper()
        self.addCleanup(shutil.rmtree, str(scripts_dir), True)
        svc = SyncService(scripts_dir=scripts_dir, timeout_seconds=3)
        started, _ = svc.trigger_async_sync()
        self.assertTrue(started)
        self.assertTrue(
            _wait_until(lambda: not svc.get_status()["is_syncing"], timeout=30.0)
        )
        status = svc.get_status()
        self.assertFalse(status["last_success"])
        self.assertIn("초과", status["last_error"])
        grandchild = self._grandchild_pid(marker)
        self.assertIsNotNone(grandchild)
        self.assertTrue(
            _wait_until(lambda: not pid_is_alive(grandchild), timeout=15.0),
            "timeout 후에도 손자 프로세스가 살아 있다",
        )

    def test_cancel_stops_running_sync_and_tree(self):
        scripts_dir, marker = self._scripts_dir_with_sleeper()
        self.addCleanup(shutil.rmtree, str(scripts_dir), True)
        svc = SyncService(scripts_dir=scripts_dir, timeout_seconds=120)
        started, _ = svc.trigger_async_sync()
        self.assertTrue(started)
        self.assertTrue(
            _wait_until(lambda: marker.exists(), timeout=15.0),
            "파이프라인 자식이 손자를 띄우지 않았다",
        )
        ok, _ = svc.cancel_sync()
        self.assertTrue(ok)
        self.assertTrue(
            _wait_until(lambda: not svc.get_status()["is_syncing"], timeout=30.0)
        )
        status = svc.get_status()
        self.assertFalse(status["last_success"])
        self.assertIn("중단", status["last_error"])
        grandchild = self._grandchild_pid(marker)
        self.assertIsNotNone(grandchild)
        self.assertTrue(
            _wait_until(lambda: not pid_is_alive(grandchild), timeout=15.0),
            "취소 후에도 손자 프로세스가 살아 있다",
        )

    def test_cancel_when_idle(self):
        svc = SyncService(
            scripts_dir=Path(tempfile.mkdtemp(prefix="followup_idle_")),
            timeout_seconds=5,
        )
        self.addCleanup(shutil.rmtree, str(svc.scripts_dir), True)
        ok, msg = svc.cancel_sync()
        self.assertFalse(ok)
        self.assertIn("없습니다", msg)


class JsonAtomicReplaceTests(unittest.TestCase):
    """ISSUE-003: 교체 실패는 기존 파일을 유지한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="followup_json_")
        self.base = Path(self.tmp)
        self.db = self.base / "data_requests.db"
        conn = sqlite3.connect(str(self.db))
        try:
            DatabaseManager.ensure_schema(conn)
        finally:
            conn.close()
        self.dest = self.base / "data_requests.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conn(self):
        return sqlite3.connect(str(self.db))

    def test_locked_destination_is_preserved(self):
        from db.json_export import export_combined_json

        conn = self._conn()
        try:
            export_combined_json(conn, self.dest)
        finally:
            conn.close()
        before = self.dest.read_bytes()
        with open(self.dest, "rb"):
            conn = self._conn()
            try:
                with self.assertRaises(RuntimeError):
                    export_combined_json(conn, self.dest)
            finally:
                conn.close()
        self.assertEqual(self.dest.read_bytes(), before)
        conn = self._conn()
        try:
            counts = export_combined_json(conn, self.dest)
        finally:
            conn.close()
        self.assertIn("documents", counts)

    def test_compact_locked_destination_is_preserved(self):
        from db.json_export import write_json_compact

        write_json_compact(self.dest, {"documents": []})
        before = self.dest.read_bytes()
        with open(self.dest, "rb"):
            with self.assertRaises(RuntimeError):
                write_json_compact(self.dest, {"documents": [{"a": 1}]})
        self.assertEqual(self.dest.read_bytes(), before)


class WorkbookUnifyTests(unittest.TestCase):
    """GAP-002: 원자 저장은 정본에 위임한다."""

    def setUp(self):
        import openpyxl

        self.tmp = tempfile.mkdtemp(prefix="followup_workbook_")
        self.base = Path(self.tmp)
        wb = openpyxl.Workbook()
        wb.active["A1"] = "값"
        self.wb = wb

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_canonical_skips_backup_for_derived_artifact(self):
        from extractors.ledger.workbook import save_workbook_atomic

        target = self.base / "통합.xlsx"
        self.assertTrue(save_workbook_atomic(self.wb, target, backup=False))
        self.assertTrue(target.exists())
        self.assertFalse((self.base / ".ledger_backup").exists())

    def test_wrapper_keeps_waiting_file_policy_on_lock(self):
        import generate_excel_db

        target = self.base / "통합.xlsx"
        self.wb.save(target)
        before = target.read_bytes()
        with mock.patch(
            "extractors.ledger.workbook.save_workbook_atomic",
            side_effect=PermissionError("잠금"),
        ):
            self.assertFalse(generate_excel_db.save_workbook_atomic(self.wb, target))
        self.assertEqual(target.read_bytes(), before)
        waiting = self.base / "통합_최신동기화_대기.xlsx"
        self.assertTrue(waiting.exists())


class SyncCancelEndpointTests(unittest.TestCase):
    """GAP-001: 취소 API와 상태 노출."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="followup_endpoint_")
        self.base = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _handler(self, path, method="GET"):
        from web_server import RequestLedgerHandler

        handler = RequestLedgerHandler.__new__(RequestLedgerHandler)
        handler.headers = {"Origin": "http://localhost:8000"}
        handler.path = path
        handler.command = method
        sent = {}

        def send_json(data, status_code=200):
            sent["status"] = status_code
            sent["data"] = data

        def send_error(status_code, message):
            sent["status"] = status_code
            sent["data"] = {"success": False, "error": message}

        handler.send_json_response = send_json
        handler.send_error_response = send_error
        return handler, sent

    def test_cancel_endpoint_when_idle(self):
        import web_server

        orig_base = web_server.BASE_DIR
        try:
            web_server.BASE_DIR = self.base
            handler, sent = self._handler("/api/sync/cancel", "POST")
            handler.do_POST()
        finally:
            web_server.BASE_DIR = orig_base
        self.assertEqual(sent["status"], 400)
        self.assertFalse(sent["data"]["success"])

    def test_sync_status_exposes_search_log_dropped(self):
        import web_server

        orig_base = web_server.BASE_DIR
        try:
            web_server.BASE_DIR = self.base
            handler, sent = self._handler("/api/sync", "GET")
            handler.do_GET()
        finally:
            web_server.BASE_DIR = orig_base
        self.assertEqual(sent["status"], 200)
        self.assertTrue(sent["data"]["success"])
        self.assertIn("search_log_dropped", sent["data"])


if __name__ == "__main__":
    unittest.main()
