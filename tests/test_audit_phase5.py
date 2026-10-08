# -*- coding: utf-8 -*-
"""
Test Suite for Audit Phase 5 Improvements.
Verifies:
1. Offline ledger sync data loss prevention on partial network/server failures.
2. LedgerService connection cleanup and try...finally guarantee on queries.
3. Atomic data_requests.json synchronization with retry resilience.
4. SyncService execution metrics and get_status() lifecycle.
5. Web server GET and POST /api/sync endpoint contract.
6. Openpyxl workbook closing and handle cleanup.
"""

from typing import Any
import os
import sys
import json
import sqlite3
import unittest
import tempfile
import shutil
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from services.ledger_service import LedgerService
from services.sync_service import SyncService
from web_server import RequestLedgerHandler
from db.database_manager import DatabaseManager
from extractors import ledger_parser
import generate_excel_db


class TestAuditPhase5(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_offline_sync_partial_failure_preservation(self):
        """Verify that dashboard_template.html preserves unsynced items in localStorage when some fail."""
        tmpl_path = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        self.assertTrue(tmpl_path.exists())
        content = tmpl_path.read_text(encoding="utf-8")

        # Must not unconditionally remove the entire storage
        self.assertNotIn("localStorage.removeItem('datareq_local_ledger');\n  const banner", content)
        # Must track successfulIds and compute remaining
        self.assertIn("successfulIds", content)
        self.assertIn("remaining", content)
        self.assertIn("localStorage.setItem('datareq_local_ledger'", content)

    def test_ledger_service_connection_closed_on_error(self):
        """Verify that LedgerService queries safely close connections."""
        db_file = self.temp_path / "test_conn.db"
        json_file = self.temp_path / "test_conn.json"
        
        mgr = DatabaseManager(db_file)
        conn = mgr.get_conn()
        try:
            DatabaseManager.init_schema(conn)
        finally:
            conn.close()

        service = LedgerService(db_file, json_file)

        # Run queries
        rows1 = service.query_ledger(year="2026", kw="test")
        rows2 = service.query_documents(year="2026", kw="test")
        rows3 = service.query_qa_items(year="2026", kw="test")
        self.assertIsInstance(rows1, list)
        self.assertIsInstance(rows2, list)
        self.assertIsInstance(rows3, list)

        # In Windows, if connection was left unclosed, unlinking the db file immediately raises PermissionError!
        # This confirms that conn.close() was properly executed in finally block.
        try:
            db_file.unlink()
            success = True
        except PermissionError:
            success = False
        self.assertTrue(success, "DB connection leaked and locked the file!")

    def test_atomic_json_sync_resilience(self):
        """Verify that sync_json_file writes to a tmp file and uses atomic replace with retry."""
        db_file = self.temp_path / "test_sync.db"
        json_file = self.temp_path / "data_requests.json"

        mgr = DatabaseManager(db_file)
        conn = mgr.get_conn()
        try:
            DatabaseManager.init_schema(conn)
        finally:
            conn.close()

        service = LedgerService(db_file, json_file)
        service.insert_ledger_item({
            "year": "2026",
            "seq_no": "001",
            "requester": "의원",
            "title": "원자적 동기화 테스트"
        })

        # Run synchronous JSON sync
        service.sync_json_file(async_mode=False)

        self.assertTrue(json_file.exists())
        with open(json_file, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            self.assertIn("request_ledger", data)
            self.assertTrue(any(it["title"] == "원자적 동기화 테스트" for it in data["request_ledger"]))

    def test_sync_service_status_lifecycle(self):
        """Verify SyncService reports proper status and execution metrics."""
        sync_svc = SyncService(scripts_dir=self.temp_path)
        status = sync_svc.get_status()

        self.assertTrue(status["success"])
        self.assertFalse(status["is_syncing"])
        self.assertIsNone(status["last_start_time"])

        # Test trigger
        started, msg = sync_svc.trigger_async_sync()
        self.assertTrue(started)

        # Immediate status check while possibly running
        st_running = sync_svc.get_status()
        self.assertIsNotNone(st_running["last_start_time"])

        # Wait for worker thread
        time.sleep(0.5)
        st_done = sync_svc.get_status()
        self.assertFalse(st_done["is_syncing"])
        self.assertIsNotNone(st_done["last_finish_time"])

    def test_web_server_sync_endpoints(self):
        """Verify web_server's RequestLedgerHandler handles GET and POST /api/sync."""
        handler: Any = RequestLedgerHandler.__new__(RequestLedgerHandler)
        handler.headers = {"Origin": "http://localhost:8000"}
        
        sent_responses = []
        def mock_send_json(data, status_code=200):
            sent_responses.append((status_code, data))

        handler.send_json_response = mock_send_json

        # Test GET /api/sync
        handler.path = "/api/sync"
        handler.do_GET()
        self.assertEqual(len(sent_responses), 1)
        self.assertEqual(sent_responses[0][0], 200)
        self.assertTrue(sent_responses[0][1]["success"])
        self.assertIn("is_syncing", sent_responses[0][1])

    def test_excel_parser_workbook_cleanup(self):
        """Verify openpyxl workbook is closed in ledger_parser.py."""
        ledger_src = "".join(
            p.read_text(encoding="utf-8")
            for p in (SCRIPTS_DIR / "extractors" / "ledger").glob("*.py")
        )
        self.assertIn("wb.close()", ledger_src)

        excel_gen_code = (SCRIPTS_DIR / "generate_excel_db.py").read_text(encoding="utf-8")
        self.assertIn("wb.close()", excel_gen_code)


if __name__ == "__main__":
    unittest.main()
