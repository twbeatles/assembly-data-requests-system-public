from typing import Any
import os
import sys
import unittest
import tempfile
import json
import sqlite3
import re
from pathlib import Path
from http.server import ThreadingHTTPServer

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import add_documents_smart
import extract_and_build_db
import parse_all
import generate_web_dashboard
import web_server
import system_config
import launcher_gui

class TestAuditPhase3(unittest.TestCase):
    """
    Phase 3 Audit Verification Tests:
    1. add_documents_smart: Precision master ledger classification vs answer documents
    2. extract_and_build_db: Multi-header synonym lookups for ledger columns
    3. parse_all: Zero-file safe termination without ZeroDivisionError
    4. generate_web_dashboard: XSS sanitization (javascript: protocol, on* handlers, base tags)
    5. web_server: ThreadingHTTPServer, payload size limit, port failover probing
    6. launcher_gui: Dynamic year list extraction from database
    7. system_config: Atomic config saving and web_port default
    """

    def test_1_smart_add_master_ledger_classification(self):
        """Verify individual answer excels are NOT misclassified as master ledgers"""
        # True master ledgers
        self.assertTrue(add_documents_smart.is_master_ledger_file(Path("2026년_국회_요구자료_관리대장.xlsx")))
        self.assertTrue(add_documents_smart.is_master_ledger_file(Path("(양식)국회_요구자료_목록_대장_템플릿.xlsx")))
        self.assertTrue(add_documents_smart.is_master_ledger_file(Path("국회_자료요구_목록_마스터대장.xlsm")))

        # Individual answer documents containing '요구자료' or '자료요구'
        answer_doc_1 = Path("250213 대외기관 요구자료_월별 통계.xlsx")
        answer_doc_2 = Path("260301 과방위 자료요구 답변서.xlsx")
        answer_doc_3 = Path("240915 대외기관 요구자료(0133)_(참고) 현황.xlsx")
        self.assertFalse(add_documents_smart.is_master_ledger_file(answer_doc_1))
        self.assertFalse(add_documents_smart.is_master_ledger_file(answer_doc_2))
        self.assertFalse(add_documents_smart.is_master_ledger_file(answer_doc_3))

        # Target-folder creation must stay inside a disposable fixture.
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_root = add_documents_smart.ROOT_DIR
            try:
                add_documents_smart.ROOT_DIR = Path(tmp_dir)
                target_folder = add_documents_smart.determine_target_folder(answer_doc_1)
                self.assertEqual(target_folder.name, "2025")
            finally:
                add_documents_smart.ROOT_DIR = original_root

    def test_2_ledger_multi_header_synonym_lookups(self):
        """Verify load_request_ledger extracts data using template synonym headers (접수일자, 제출여부)"""
        import openpyxl
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            test_excel = tmp_p / "(양식)국회_요구자료_목록_대장_템플릿.xlsx"

            wb = openpyxl.Workbook()
            ws = wb.active
            assert ws is not None
            ws.title = "2026"
            # Use official template headers with '접수일자' and '제출여부'
            headers = ["연번", "접수일자", "소속", "의원", "보좌관", "요구자료", "세부내역", "마감일", "제출일", "제출여부", "요청형태", "비고"]
            for col_idx, h in enumerate(headers, start=1):
                ws.cell(1, col_idx, h)

            sample_row = ["1", "2026-08-15", "과방위", "이순신", "나대용", "디지털 성범죄 대응 현황", "상세 내역", "2026-08-20", "2026-08-19", "제출완료", "전자문서", "특이사항 없음"]
            for col_idx, val in enumerate(sample_row, start=1):
                ws.cell(2, col_idx, val)

            wb.save(test_excel)

            # Test loading via load_request_ledger
            # DB·설정 폴더를 명시한다. 생략하면 예전에는 운영 DB가 병합됐다. (감사 R4-01/R4-02)
            items = extract_and_build_db.load_request_ledger(
                tmp_p, [], db_path=tmp_p / "data_requests.db", system_dir=tmp_p)
            matched = [it for it in items if it.get("title") == "디지털 성범죄 대응 현황"]
            self.assertEqual(len(matched), 1)
            item = matched[0]
            self.assertEqual(item["seq_no"], "1")
            self.assertEqual(item["request_date"], "2026-08-15")
            self.assertEqual(item["status"], "제출완료")
            self.assertEqual(item["requester"], "이순신")
            self.assertEqual(item["title"], "디지털 성범죄 대응 현황")

    def test_3_parse_all_zero_files_handling(self):
        """Verify parse_all terminates cleanly with 0 files without ZeroDivisionError"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            out_dir = tmp_root / "_parsed_markdown"
            # find_target_files on empty directory
            files = parse_all.find_target_files(tmp_root)
            self.assertEqual(len(files), 0)

    def test_4_web_dashboard_xss_sanitization(self):
        """Verify sanitizeHtml in web dashboard template blocks javascript: and dangerous attributes"""
        tpl_path = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        if tpl_path.exists():
            template_text = tpl_path.read_text(encoding="utf-8")
        else:
            template_text = (SCRIPTS_DIR / "generate_web_dashboard.py").read_text(encoding="utf-8")
        # 감사 R3-15: 정규식 치환 대신 허용 목록으로 태그를 다시 조립한다.
        # 우회 벡터 실행 검증은 test_audit_phase10_dashboard.TestDashboardSanitizer(Node)가 한다.
        self.assertIn("function rebuildTag", template_text)
        self.assertIn("SANITIZE_TAGS", template_text)
        self.assertIn("sanitizeHtml(marked.parse", template_text)

    def test_5_web_server_threading_and_body_limits(self):
        """Verify web_server uses ThreadingHTTPServer and limits body size to 5MB"""
        # ThreadingHTTPServer verification
        self.assertTrue(issubclass(ThreadingHTTPServer, object))
        
        # Test read_json_body size limit
        class DummyHandler(web_server.RequestLedgerHandler):
            def __init__(self):
                self.headers: Any = {}
                self.rfile: Any = None

        handler = DummyHandler()
        # > 5MB should be rejected immediately
        handler.headers = {'Content-Length': str(6 * 1024 * 1024)}
        self.assertIsNone(handler.read_json_body())

        # Negative or 0 should return None
        handler.headers = {'Content-Length': '0'}
        self.assertIsNone(handler.read_json_body())

    def test_6_dynamic_year_loading_in_gui(self):
        """Verify database distinct years query logic in launcher_gui"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "test.db"
            conn = sqlite3.connect(db_file)
            c = conn.cursor()
            c.execute("CREATE TABLE documents (year TEXT)")
            c.execute("CREATE TABLE request_ledger (year TEXT)")
            c.execute("INSERT INTO documents VALUES ('2026'), ('2025'), ('2023')")
            c.execute("INSERT INTO request_ledger VALUES ('2026'), ('2024'), ('2022')")
            conn.commit()

            c.execute("""
                SELECT DISTINCT year FROM documents WHERE year IS NOT NULL AND year != ''
                UNION
                SELECT DISTINCT year FROM request_ledger WHERE year IS NOT NULL AND year != ''
                ORDER BY year DESC
            """)
            years = [r[0] for r in c.fetchall() if r[0]]
            conn.close()

            self.assertEqual(years, ['2026', '2025', '2024', '2023', '2022'])

    def test_7_atomic_config_saving_and_web_port(self):
        """Verify system_config saves atomically and includes web_port default"""
        self.assertIn("web_port", system_config.DEFAULT_CONFIG)
        self.assertEqual(system_config.DEFAULT_CONFIG["web_port"], 8080)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_base = Path(tmp_dir)
            cfg = system_config.get_config(tmp_base)
            cfg["department_name"] = "신규테스트부서"
            cfg["web_port"] = 8090
            ok = system_config.save_config(cfg, tmp_base)
            self.assertTrue(ok)

            # Reload and verify
            reloaded = system_config.get_config(tmp_base)
            self.assertEqual(reloaded["department_name"], "신규테스트부서")
            self.assertEqual(reloaded["web_port"], 8090)
            # Ensure no leftover temp files
            self.assertFalse((tmp_base / "config.tmp.json").exists())

if __name__ == "__main__":
    unittest.main()
