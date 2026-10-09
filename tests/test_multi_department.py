from typing import Any
import unittest
import os
import sys
import json
import shutil
import tempfile
import sqlite3
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SYSTEM_DIR / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_config
import web_server
import extract_and_build_db
import build_universal_package
import create_ledger_template
from template_renderer import DashboardRenderer

class TestMultiDepartmentAndPackaging(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_dept_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_system_config_defaults_and_override(self):
        """Test system_config loads defaults when no config.json, and updates cleanly."""
        cfg = system_config.get_config(self.temp_dir)
        self.assertIn("department_name", cfg)
        self.assertIn("system_title", cfg)

        # Save new custom config
        custom = {
            "department_name": "테스트운영팀",
            "agency_name": "공공기관",
            "system_title": "자료요구 통합 테스트 시스템"
        }
        ok = system_config.save_config(custom, self.temp_dir)
        self.assertTrue(ok)
        self.assertTrue((self.temp_dir / "config.json").exists())

        loaded = system_config.get_config(self.temp_dir)
        self.assertEqual(loaded["department_name"], "테스트운영팀")
        self.assertEqual(loaded["system_title"], "자료요구 통합 테스트 시스템")

    def test_universal_package_structure(self):
        """Test build_universal_package creates all required clean files."""
        dist_out = self.temp_dir / "test_universal_pkg"
        build_universal_package.build_universal_package(dist_out)

        self.assertTrue((dist_out / "00_새자료_추가_및_DB동기화.bat").exists())
        self.assertTrue((dist_out / "01_웹대시보드_실행.bat").exists())
        self.assertTrue((dist_out / "02_웹관리서버_실행.bat").exists())
        self.assertTrue((dist_out / "03_엑셀DB_열기.bat").exists())
        self.assertTrue((dist_out / "04_부서명_간편설정.bat").exists())
        self.assertTrue((dist_out / "07_기존자료_안전마이그레이션.bat").exists())
        self.assertTrue((dist_out / "config.json").exists())
        self.assertTrue((dist_out / "(양식)국회_요구자료_목록_대장_템플릿.xlsx").exists())
        self.assertTrue((dist_out / "새자료_투입폴더").is_dir())
        self.assertTrue((dist_out / "새자료_투입폴더" / "README_여기에_파일을_넣으세요.txt").exists())
        self.assertTrue((dist_out / "data_requests.db").exists())
        self.assertTrue((dist_out / "data_requests.json").exists())
        self.assertTrue((dist_out / "자료요구_통합검색_대시보드.html").exists())
        # Distribution package must NOT have Excel DB loaded as per user requirement
        self.assertFalse((dist_out / "국회_기관_자료요구_통합DB.xlsx").exists())
        self.assertTrue((dist_out / "사용설명서_및_안내.html").exists())
        self.assertTrue((dist_out / "README_배포안내.txt").exists())

        # Verify DB is clean (0 records)
        conn = sqlite3.connect(str(dist_out / "data_requests.db"))
        cur = conn.cursor()
        doc_c = cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        qa_c = cur.execute("SELECT COUNT(*) FROM qa_items").fetchone()[0]
        led_c = cur.execute("SELECT COUNT(*) FROM request_ledger").fetchone()[0]
        conn.close()

        self.assertEqual(doc_c, 0)
        self.assertEqual(qa_c, 0)
        self.assertEqual(led_c, 0)

    def test_html_only_dashboard_is_self_contained_with_full_text(self):
        """관리자가 HTML 한 파일만 전달해도 검색·전문 열람이 가능해야 한다."""
        raw = {
            "documents": [{"doc_id": "DOC-1", "title": "테스트", "full_markdown": "문서 전문 전체"}],
            "qa_items": [{"qa_id": "QA-1", "doc_id": "DOC-1", "answer_markdown": "Q&A 답변 전문"}],
            "request_ledger": [{"ledger_id": "REQ-1", "title": "대장"}],
        }
        renderer = DashboardRenderer()
        html = renderer.render(
            renderer.load_template(),
            "window.marked = { parse: text => text };",
            renderer.compress_payload(renderer.build_slim_payload(raw)),
            {"department_name": "테스트부서", "system_title": "테스트 시스템"},
        )
        ok, error = renderer.verify_offline_dashboard_html(html)
        self.assertTrue(ok, error)

    def test_web_server_api_and_empty_db(self):
        """Test web_server handles empty database and config endpoint correctly."""
        web_server.BASE_DIR = self.temp_dir
        web_server.DB_PATH = self.temp_dir / "data_requests.db"
        web_server.JSON_PATH = self.temp_dir / "data_requests.json"

        # Initially no DB file exists
        handler: Any = web_server.RequestLedgerHandler
        stats = handler.get_stats(None)
        self.assertTrue(stats["success"])
        self.assertEqual(stats["documents"], 0)
        self.assertEqual(stats["qa_items"], 0)
        self.assertEqual(stats["request_ledger"], 0)

        # Test insert ledger item with default department
        system_config.save_config({"department_name": "신규테스트부서"}, self.temp_dir)
        insert_res = handler.insert_ledger_item(None, {
            "title": "테스트 요구자료 제목",
            "requester": "홍길동의원",
            "year": "2026"
        })
        self.assertTrue(insert_res["success"])
        self.assertEqual(insert_res["item"]["department"], "신규테스트부서")

        # Stats should now show 1 ledger item
        new_stats = handler.get_stats(None)
        self.assertEqual(new_stats["request_ledger"], 1)

    def test_pipeline_with_no_ledger(self):
        """Test extract_and_build_db completes gracefully even when no ledger excel exists."""
        fake_sys_dir = self.temp_dir / "app"
        fake_sys_dir.mkdir(parents=True)
        (fake_sys_dir / "_parsed_markdown").mkdir()

        # Mock module variables
        orig_sys = extract_and_build_db.SYSTEM_DIR
        orig_root = extract_and_build_db.ROOT_DIR
        orig_db = extract_and_build_db.DB_PATH
        orig_json = extract_and_build_db.JSON_PATH

        try:
            extract_and_build_db.SYSTEM_DIR = fake_sys_dir
            extract_and_build_db.ROOT_DIR = fake_sys_dir
            extract_and_build_db.DB_PATH = fake_sys_dir / "data_requests.db"
            extract_and_build_db.JSON_PATH = fake_sys_dir / "data_requests.json"

            # Run build with 0 documents and 0 ledger
            ledger_items = extract_and_build_db.load_request_ledger(fake_sys_dir, [])
            self.assertEqual(len(ledger_items), 0)

            # Create an excel template and test loading
            tpl_path = fake_sys_dir / "임시대장_요구자료_목록.xlsx"
            create_ledger_template.create_template(tpl_path)
            ledger_items2 = extract_and_build_db.load_request_ledger(fake_sys_dir, [])
            # Template has 1 sample row in 2026 and 1 in 2025 = 2 rows
            self.assertEqual(len(ledger_items2), 2)
        finally:
            extract_and_build_db.SYSTEM_DIR = orig_sys
            extract_and_build_db.ROOT_DIR = orig_root
            extract_and_build_db.DB_PATH = orig_db
            extract_and_build_db.JSON_PATH = orig_json

if __name__ == '__main__':
    unittest.main()
