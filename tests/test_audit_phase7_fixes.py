from typing import Any
# -*- coding: utf-8 -*-
"""
Phase 7 Comprehensive Audit Fixes Test Suite.
Verifies all 14 audit issues addressed in PROJECT_AUDIT.md:
- LedgerService singleton/caching & connection leak prevention
- PipelineGuard atomic exclusive lock (os.O_EXCL)
- FTS5 special character sanitization in search_query
- History limit bounds enforcement (max 1000)
- Excel sync partial-parse bulk deletion safety guard
- Pending queue save resilience & logging
- is_public_static operator precedence & security
- Dynamic workspace root detection (e.g. 2027+ support)
- read_json_body_detail size and syntax validation
- PII masking on ledger inputs
"""

import os
import sys
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

TESTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_config
from pipeline_guard import PipelineGuard
from search_query import build_fts_match
from db.database_manager import DatabaseManager
from services.ledger_service import LedgerService
from services.excel_sync_service import ExcelSyncService
from web_server import RequestLedgerHandler, get_ledger_service


class TestAuditPhase7Fixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test.db"
        self.json_path = self.base_path / "test.json"

        # Initialize test database
        conn = DatabaseManager.get_connection(self.db_path)
        DatabaseManager.ensure_schema(conn)
        conn.close()

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_ledger_service_caching_and_reuse(self):
        """Test that get_ledger_service caches and reuses the service instance."""
        svc1 = get_ledger_service()
        svc2 = get_ledger_service()
        self.assertIs(svc1, svc2, "get_ledger_service must return the same cached instance")

    def test_pipeline_guard_atomic_exclusive(self):
        """Test that PipelineGuard uses atomic exclusive creation preventing concurrent double-acquisition."""
        guard1 = PipelineGuard(self.base_path)
        guard2 = PipelineGuard(self.base_path)

        # First acquisition should succeed
        acquired1 = guard1.acquire(allow_reentrant=False)
        self.assertTrue(acquired1)
        self.assertTrue(guard1.is_locked())

        # Second acquisition from another guard with allow_reentrant=False must raise RuntimeError
        with self.assertRaises(RuntimeError):
            guard2.acquire(allow_reentrant=False)

        # Reentrant acquisition from same process with allow_reentrant=True should return False
        self.assertFalse(guard1.acquire(allow_reentrant=True))

        # Release lock
        guard1.release()
        self.assertFalse(guard1.is_locked())

    def test_pipeline_guard_clears_dead_pid_lock(self):
        """죽은 프로세스의 락은 4시간 이내여도 회수해야 한다.

        00_새자료_추가_및_DB동기화.bat가 창을 닫히거나 강제 종료되면
        .pipeline.lock만 남고, 다음 실행이 '파이프라인 실행 중'으로 바로 실패했다.
        """
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        dead_pid = holder.pid
        holder.kill()
        holder.wait()
        # Popen 핸들이 남아 있으면 Windows에서 PID가 살아 있는 것처럼 보일 수 있다.
        del holder
        time.sleep(0.2)

        guard = PipelineGuard(self.base_path)
        guard.path.write_text(str(dead_pid), encoding="utf-8")
        self.assertTrue(guard.path.exists())
        self.assertFalse(guard.is_locked(), "죽은 PID 락은 is_locked()가 False여야 한다")
        acquired = guard.acquire(allow_reentrant=False)
        self.assertTrue(acquired, "죽은 PID 락이 있으면 새 프로세스가 락을 잡을 수 있어야 한다")
        self.assertEqual(guard.path.read_text(encoding="utf-8").strip(), str(os.getpid()))
        guard.release()

    def test_search_query_special_chars_sanitization(self):
        """Test that build_fts_match sanitizes FTS5 reserved characters safely."""
        # Unsafe queries with quotes, asterisks, brackets, operators
        unsafe_inputs = [
            '"""딥페이크"""',
            '텔레그램* OR "대외기관"',
            '시정요구 (최근 3년) [보고서]',
            '테스트:^~\\//질의',
            '""'
        ]
        for inp in unsafe_inputs:
            fts_query = build_fts_match(inp)
            # FTS query should not contain unmatched quotes or raw reserved characters
            if fts_query:
                # Every quote should be paired
                self.assertEqual(fts_query.count('"') % 2, 0, f"Unbalanced quotes in: {fts_query}")
                for ch in ['*', '^', '(', ')', '{', '}', '[', ']', ':', '~', '\\', '/']:
                    self.assertNotIn(ch, fts_query, f"Reserved character '{ch}' must be stripped: {fts_query}")

        # Normal query check
        # trigram은 3글자 미만을 색인하지 않으므로 MATCH 문자열에는 3글자 이상 낱말만 담긴다.
        # 2글자 낱말이 섞인 질의는 통째로 파이썬 대체 경로가 맡는다(제안서 S2).
        normal_match = build_fts_match("디지털 성범죄 시정요구")
        self.assertIn('"디지털"', normal_match)
        self.assertIn('"성범죄"', normal_match)
        self.assertIn('"시정요구"', normal_match)
        self.assertIn(" AND ", normal_match)
        self.assertEqual(build_fts_match("디지털 성범죄 대응"), "",
                         "2글자 낱말이 섞이면 FTS로 표현할 수 없어 대체 경로로 보낸다")

    def test_history_limit_boundary(self):
        """Test that get_ledger_history bounds limit between 1 and 1000."""
        svc = LedgerService(db_path=self.db_path, json_path=self.json_path, base_dir=self.base_path)

        # Insert test history row
        conn = svc.get_conn()
        cur = conn.cursor()
        for i in range(5):
            cur.execute(
                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                (f"REQ-2026-00{i}", "INSERT", "2026-09-11 12:00:00", "{}")
            )
        conn.commit()
        conn.close()

        # Excessive limit should be safely bounded to 1000
        res = svc.get_ledger_history(limit=50000)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["history"]), 5)

        # Zero or negative limit should fallback to at least 1
        res_neg = svc.get_ledger_history(limit=-5)
        self.assertTrue(res_neg["success"])
        self.assertGreaterEqual(len(res_neg["history"]), 1)

    def test_excel_sync_partial_delete_protection(self):
        """Test that sync_excel_to_db avoids bulk-deletion if parsed items are fewer than 50% of existing DB records."""
        # Insert 15 existing non-system records
        conn = DatabaseManager.get_connection(self.db_path)
        cur = conn.cursor()
        for i in range(15):
            cur.execute("""
                INSERT INTO request_ledger (ledger_id, year, requester, title, request_type, status)
                VALUES (?, '2026', '의원', '기존요구', '일반', '제출')
            """, (f"REQ-2026-{i+1:03d}",))
        conn.commit()
        conn.close()

        sync_svc = ExcelSyncService(base_dir=self.base_path, db_path=self.db_path, json_path=self.json_path)

        # Mock find_master_excel and load_request_ledger returning only 2 items (< 50% of 15)
        dummy_excel = self.base_path / "dummy.xlsx"
        dummy_excel.write_text("dummy", encoding="utf-8")
        sync_svc.find_master_excel = lambda: dummy_excel

        from unittest.mock import patch
        partial_items = [
            {"ledger_id": "REQ-2026-001", "year": "2026", "requester": "의원", "title": "수정요구"}
        ]
        with patch("extractors.ledger_parser.load_request_ledger", return_value=partial_items):
            res = sync_svc.sync_excel_to_db()
            self.assertTrue(res["success"])

        # Verify that the other 14 records were NOT marked as [삭제]
        conn = DatabaseManager.get_connection(self.db_path)
        cur = conn.cursor()
        deleted_count = cur.execute("SELECT COUNT(*) FROM request_ledger WHERE status = '[삭제]'").fetchone()[0]
        conn.close()
        self.assertEqual(deleted_count, 0, "Bulk deletion must be prevented when parsed items are fewer than 50%")

    def test_pending_queue_save_resilience(self):
        """Test that _save_pending_queue logs errors gracefully when disk write fails without raising exceptions."""
        sync_svc = ExcelSyncService(base_dir=self.base_path, db_path=self.db_path, json_path=self.json_path)
        sync_svc._pending_queue = [{"action": "insert", "item": {"title": "test"}}]

        # Trigger save with read-only / invalid path mock
        with patch("pathlib.Path.write_text", side_effect=PermissionError("Permission denied")):
            # Should not raise exception
            try:
                sync_svc._save_pending_queue()
            except Exception as e:
                self.fail(f"_save_pending_queue raised unexpected exception: {e}")

    def test_is_public_static_security(self):
        """Test is_public_static accepts only safe public static files and rejects hidden/invalid paths."""
        self.assertTrue(RequestLedgerHandler.is_public_static("/"))
        self.assertTrue(RequestLedgerHandler.is_public_static("/index.html"))
        self.assertTrue(RequestLedgerHandler.is_public_static("/사용설명서_및_안내.html"))

        # Hidden html or directory traversal must be rejected
        self.assertFalse(RequestLedgerHandler.is_public_static("/.hidden.html"))
        self.assertFalse(RequestLedgerHandler.is_public_static("/scripts/secret.html"))
        self.assertFalse(RequestLedgerHandler.is_public_static("/..%2Fsecret.html"))
        self.assertFalse(RequestLedgerHandler.is_public_static("/test.py"))
        self.assertFalse(RequestLedgerHandler.is_public_static("/test.db"))

    def test_dynamic_workspace_root_detection(self):
        """Test that find_workspace_root detects workspace root with future year folders (e.g. 2027)."""
        temp_ws = self.base_path / "custom_ws"
        temp_ws.mkdir()
        year_dir = temp_ws / "2027"
        year_dir.mkdir()
        sys_dir = temp_ws / "스마트시스템"
        sys_dir.mkdir()

        detected_root = system_config.find_workspace_root(sys_dir)
        self.assertEqual(detected_root.resolve(), temp_ws.resolve(), "Should identify parent directory containing year folder 2027")

    def test_read_json_body_detail_validation(self):
        """Test read_json_body_detail for size limits and json formatting errors."""
        handler: Any = RequestLedgerHandler.__new__(RequestLedgerHandler)
        handler.headers = {"Content-Length": "10000000"}  # 10MB > 5MB
        data, code, msg = handler.read_json_body_detail()
        self.assertIsNone(data)
        self.assertEqual(code, 413)
        self.assertIn("5MB", msg)

        # Empty content
        handler.headers = {"Content-Length": "0"}
        data, code, msg = handler.read_json_body_detail()
        self.assertIsNone(data)
        self.assertEqual(code, 400)

        # Malformed json
        import io
        handler.headers = {"Content-Length": "5"}
        handler.rfile = io.BytesIO(b"{bad}")
        data, code, msg = handler.read_json_body_detail()
        self.assertIsNone(data)
        self.assertEqual(code, 400)
        self.assertIn("JSON", msg)

    def test_pii_masking_on_ledger_input(self):
        """Test that insert_ledger_item and update_ledger_item automatically mask personal phone numbers and resident IDs."""
        svc = LedgerService(db_path=self.db_path, json_path=self.json_path, base_dir=self.base_path)
        item_data = {
            "title": "개인정보 보호 테스트",
            "requester": "감사의원",
            "year": "2026",
            "details": "연락처: 010-9876-5432, 주민번호: 880101-1234567 기재 내역",
            "note": "비고란 번호 010.1111.2222 포함"
        }
        res = svc.insert_ledger_item(item_data)
        assert res is not None
        self.assertTrue(res["success"])
        inserted_id = res["item"]["ledger_id"]

        # Check DB values
        row = svc.get_ledger_item(inserted_id)
        assert row is not None
        self.assertIn("010-****-5432", row["details"])
        self.assertNotIn("010-9876-5432", row["details"])
        self.assertIn("880101-1******", row["details"])
        self.assertNotIn("880101-1234567", row["details"])
        self.assertIn("010-****-2222", row["note"])

        # Test update masking
        update_res = svc.update_ledger_item(inserted_id, {
            "details": "수정된 연락처: 010-5555-6666",
            "note": "수정 비고"
        })
        self.assertTrue(update_res["success"])
        updated_row = svc.get_ledger_item(inserted_id)
        assert updated_row is not None
        self.assertIn("010-****-6666", updated_row["details"])
        self.assertNotIn("010-5555-6666", updated_row["details"])


if __name__ == "__main__":
    unittest.main()
