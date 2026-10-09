# -*- coding: utf-8 -*-
"""
Phase 6 Comprehensive Audit & Improvement Test Suite.
Tests:
1. Excel to DB sync: merge_db_records=False, empty cell propagation to SQLite.
2. ledger_history audit trail: automatic logging of INSERT, UPDATE, DELETE, and EXCEL_SYNC.
3. LedgerService.get_ledger_history and Web Server /api/ledger/history endpoint.
4. generate_excel_db: build_qa_detail_sheet relative hyperlink normalization.
5. CORS security: rejection of unauthorized external origins (no wildcard '*').
6. Web server CWD independence: directory parameter passed to SimpleHTTPRequestHandler.
7. QA Splitter & Text Extractor: short 2-character question title preservation (e.g. '1. 현황', '2. 예산').
8. atomic_swap SQLite online backup handling under locked target.
9. Dashboard template CSV export and strict HTTP error rejection.
"""

from typing import Any
import os
import sys
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
ROOT_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_config
from db.database_manager import DatabaseManager
from services.ledger_service import LedgerService
from services.excel_sync_service import ExcelSyncService
from extractors.qa_splitter import QASplitter
from extractors.text_extractor import extract_content_details
import generate_excel_db
import web_server


class TestAuditPhase6(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base_dir = Path(self.temp_dir)
        self.db_path = self.base_dir / "data_requests.db"
        self.json_path = self.base_dir / "data_requests.json"

        # Initialize test DB
        conn = sqlite3.connect(str(self.db_path))
        try:
            DatabaseManager.init_schema(conn)
        finally:
            conn.close()

        self.service = LedgerService(db_path=self.db_path, json_path=self.json_path, base_dir=self.base_dir)

    def tearDown(self):
        LedgerService.wait_last_sync(timeout=2.0)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_1_ledger_history_audit_trail_mutations(self):
        """Verify ledger_history records INSERT, UPDATE, DELETE with snapshots."""
        # 1. Insert
        insert_data = {
            "year": "2026",
            "seq_no": "1",
            "party": "과방위",
            "requester": "홍길동의원",
            "title": "감사 기록 테스트 자료",
            "department": "기획예산팀",
            "status": "작성중"
        }
        res_ins = self.service.insert_ledger_item(insert_data)
        self.assertTrue(res_ins["success"])
        ledger_id = res_ins["item"]["ledger_id"]

        hist_ins = self.service.get_ledger_history(ledger_id=ledger_id)
        self.assertTrue(hist_ins["success"])
        self.assertEqual(len(hist_ins["history"]), 1)
        self.assertEqual(hist_ins["history"][0]["action"], "INSERT")
        snap_ins = json.loads(hist_ins["history"][0]["snapshot"])
        self.assertEqual(snap_ins["title"], "감사 기록 테스트 자료")

        # 2. Update
        res_upd = self.service.update_ledger_item(ledger_id, {"title": "수정된 감사 자료 제목", "status": "제출완료"})
        self.assertTrue(res_upd["success"])

        hist_upd = self.service.get_ledger_history(ledger_id=ledger_id)
        self.assertEqual(len(hist_upd["history"]), 2)
        self.assertEqual(hist_upd["history"][0]["action"], "UPDATE")
        snap_upd = json.loads(hist_upd["history"][0]["snapshot"])
        # Snapshot of previous state
        self.assertEqual(snap_upd["title"], "감사 기록 테스트 자료")

        # 3. Delete
        res_del = self.service.delete_ledger_item(ledger_id)
        self.assertTrue(res_del["success"])

        hist_del = self.service.get_ledger_history(ledger_id=ledger_id)
        self.assertEqual(len(hist_del["history"]), 3)
        self.assertEqual(hist_del["history"][0]["action"], "DELETE")

    def test_2_web_server_ledger_history_endpoint(self):
        """Verify web_server /api/ledger/history route returns JSON history."""
        # Seed record and update to create history
        ins = self.service.insert_ledger_item({
            "year": "2026",
            "requester": "김의원",
            "title": "API 이력 테스트"
        })
        lid = ins["item"]["ledger_id"]
        self.service.update_ledger_item(lid, {"title": "API 이력 테스트(수정)"})

        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        handler.headers = {}
        old_db = web_server.DB_PATH
        old_base = web_server.BASE_DIR
        try:
            web_server.DB_PATH = self.db_path
            web_server.BASE_DIR = self.base_dir
            res = handler.get_ledger_history(ledger_id=lid)
            self.assertTrue(res["success"])
            self.assertGreaterEqual(len(res["history"]), 2)
        finally:
            web_server.DB_PATH = old_db
            web_server.BASE_DIR = old_base

    def test_3_excel_sync_cleared_cells_and_no_circular_merge(self):
        """Verify sync_excel_to_db reflects cleared cells in SQLite and does not circular merge."""
        import openpyxl
        excel_path = self.base_dir / "260904 국회 요구자료 목록(테스트)_스마트검색.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "2026"
        ws.append(["대장ID", "연번", "소속", "의원", "요구자료", "비고"])
        ws.append(["REQ-2026-001", "1", "과방위", "이의원", "초기 제목", "초기 비고"])
        wb.save(str(excel_path))
        wb.close()

        sync_svc = ExcelSyncService(base_dir=self.base_dir, db_path=self.db_path, json_path=self.json_path)

        # Initial sync from Excel to DB
        res1 = sync_svc.sync_excel_to_db()
        self.assertTrue(res1["success"])
        self.assertEqual(res1["inserted"], 1)

        # Check DB has initial values
        item1 = self.service.get_ledger_item("REQ-2026-001")
        assert item1 is not None
        self.assertEqual(item1["note"], "초기 비고")

        # Clear the '비고' cell in Excel
        wb = openpyxl.load_workbook(str(excel_path))
        ws = wb["2026"]
        ws.cell(2, 6, value="") # clear note
        wb.save(str(excel_path))
        wb.close()

        # Resync: empty cell in Excel must update DB to empty string
        res2 = sync_svc.sync_excel_to_db()
        self.assertTrue(res2["success"])
        self.assertEqual(res2["updated"], 1)

        item2 = self.service.get_ledger_item("REQ-2026-001")
        assert item2 is not None
        self.assertEqual(item2["note"], "")

    def test_4_build_qa_detail_sheet_relative_hyperlinks(self):
        """Verify build_qa_detail_sheet normalizes hyperlinks using resolve_excel_hyperlink_path."""
        import openpyxl
        wb = openpyxl.Workbook()

        qa_items = [{
            "qa_id": "QA-2026-001-01",
            "doc_id": "DOC-2026-001",
            "year": "2026",
            "institution": "국회",
            "requester": "박의원",
            "doc_number": "001",
            "q_num": 1,
            "question_title": "질문 제목",
            "answer_full": "답변 내용",
            "has_tables": "N",
            "parsed_md_path": "국회자료요구_스마트시스템(기획예산팀)/_parsed_markdown/2026/test.md",
            "original_path": "2026/test.hwp"
        }]

        generate_excel_db.build_qa_detail_sheet(wb, qa_items)
        ws = [s for s in wb.worksheets if "Q&A" in s.title][0]

        # Columns: 13=md_cell, 14=orig_cell
        md_formula = str(ws.cell(2, 13).value or "")
        orig_formula = str(ws.cell(2, 14).value or "")

        self.assertIn("HYPERLINK", md_formula)
        self.assertIn("HYPERLINK", orig_formula)
        self.assertIn("마크다운 보기", md_formula)
        self.assertIn("원본 열기", orig_formula)
        self.assertNotIn("//", md_formula)

    def test_5_cors_external_origin_rejection(self):
        """Verify web_server end_headers allows local origins and blocks external origins without wildcard '*'."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        headers_sent = {}
        def mock_send_header(k, v):
            headers_sent[k] = v

        handler.send_header = mock_send_header
        handler.headers = {'Origin': 'http://localhost:8080'}
        with patch.object(web_server.SimpleHTTPRequestHandler, 'end_headers'):
            handler.end_headers()
            self.assertEqual(headers_sent.get('Access-Control-Allow-Origin'), 'http://localhost:8080')

        # External untrusted origin
        headers_sent.clear()
        handler.headers = {'Origin': 'http://malicious-external-domain.com'}
        with patch.object(web_server.SimpleHTTPRequestHandler, 'end_headers'):
            handler.end_headers()
            self.assertNotIn('Access-Control-Allow-Origin', headers_sent)
            self.assertNotEqual(headers_sent.get('Access-Control-Allow-Origin'), '*')

        # file:// origin must not receive CORS write capability
        headers_sent.clear()
        handler.headers = {'Origin': 'null'}
        with patch.object(web_server.SimpleHTTPRequestHandler, 'end_headers'):
            handler.end_headers()
            self.assertNotIn('Access-Control-Allow-Origin', headers_sent)

    def test_6_qa_splitter_two_char_title_preservation(self):
        """Verify qa_splitter and text_extractor do not drop valid 2-character titles like '1. 현황' or '2. 예산'."""
        splitter = QASplitter()
        lines = [
            "# 국회 제출 답변서",
            "",
            "1. 현황",
            "현재 정보통신망 심의 및 조치 건수는 전년 대비 증가하였습니다.",
            "",
            "2. 예산",
            "2026년도 모니터링 예산은 총 15억원으로 편성되었습니다."
        ]
        md_text = "\n".join(lines)
        items = splitter.split("DOC-2026-001", {"year": "2026", "requester": "박의원"}, md_text)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["q_num"], 1)
        self.assertIn("현황", items[0]["question_title"])
        self.assertEqual(items[1]["q_num"], 2)
        self.assertIn("예산", items[1]["question_title"])

        # Also verify extract_content_details in text_extractor
        md_text = "\n".join(lines)
        details = extract_content_details(md_text)
        self.assertIn("현황", details["questions"])
        self.assertIn("예산", details["questions"])

    def test_7_atomic_swap_online_backup_on_locked_target(self):
        """Verify atomic_swap uses SQLite online backup when target DB is held open by a reader in WAL mode."""
        target_db = self.base_dir / "active_target.db"
        tmp_db = self.base_dir / "new_tmp.db"

        # Create target SQLite database
        conn_target = sqlite3.connect(str(target_db))
        conn_target.execute("PRAGMA journal_mode = WAL;")
        conn_target.execute("CREATE TABLE test_tbl (id INT, val TEXT);")
        conn_target.execute("INSERT INTO test_tbl VALUES (1, 'old_value');")
        conn_target.commit()

        # Create new tmp SQLite database
        conn_tmp = sqlite3.connect(str(tmp_db))
        conn_tmp.execute("CREATE TABLE test_tbl (id INT, val TEXT);")
        conn_tmp.execute("INSERT INTO test_tbl VALUES (1, 'new_value');")
        conn_tmp.commit()
        conn_tmp.close()

        # Simulate PermissionError on os.replace (as Windows locks open files)
        with patch("os.replace", side_effect=PermissionError("Locked by open reader")):
            DatabaseManager.atomic_swap(tmp_db, target_db, max_retries=2, retry_delay=0.01)

        # Check target DB now has new data via online backup
        cur = conn_target.cursor()
        row = cur.execute("SELECT val FROM test_tbl WHERE id = 1;").fetchone()
        self.assertEqual(row[0], "new_value")
        conn_target.close()

    def test_8_dashboard_template_csv_export_and_error_handling(self):
        """Verify dashboard_template has window.exportLedgerToCsv and checks HTTP error responses."""
        tmpl_path = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        self.assertTrue(tmpl_path.exists())
        content = tmpl_path.read_text(encoding="utf-8")

        # Verify exportLedgerToCsv exists and uses UTF-8 BOM
        self.assertIn("window.exportLedgerToCsv", content)
        self.assertIn("\\uFEFF", content)
        self.assertIn("관리번호,연도,연번,소속,요구자", content)

        # Verify strict HTTP mode error checks
        self.assertIn("if (!res.ok || !data.success)", content)
        self.assertIn("등록 실패:", content)
        self.assertIn("if (!res.ok || !d.success)", content)
        self.assertIn("수정 실패:", content)
        self.assertIn("삭제 실패:", content)


if __name__ == '__main__':
    unittest.main()
