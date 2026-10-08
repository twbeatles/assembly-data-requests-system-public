# -*- coding: utf-8 -*-
"""SCOPE3 감사 개선 회귀 테스트."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import add_documents_smart
import extract_and_build_db
from extractors.text_extractor import extract_metadata
from search_query import build_fts_match
from pipeline_guard import PipelineGuard
from services.ledger_service import LedgerService
from db.database_manager import DatabaseManager
import build_universal_package
import write_all_bats
import package_distribution
import launcher_gui


class TestAuditScope3(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_determine_target_folder_without_year_goes_unclassified(self):
        orig = add_documents_smart.ROOT_DIR
        try:
            add_documents_smart.ROOT_DIR = self.base
            target = add_documents_smart.determine_target_folder(
                Path("홍길동_의원_요구자료(831).hwp")
            )
            self.assertEqual(target.name, "미분류")
            self.assertTrue(target.exists())
        finally:
            add_documents_smart.ROOT_DIR = orig

    def test_determine_target_folder_yymmdd_still_works(self):
        orig = add_documents_smart.ROOT_DIR
        try:
            add_documents_smart.ROOT_DIR = self.base
            target = add_documents_smart.determine_target_folder(
                Path("250213 대외기관 요구자료_월별 통계.xlsx")
            )
            self.assertEqual(target.name, "2025")
        finally:
            add_documents_smart.ROOT_DIR = orig

    def test_extract_metadata_does_not_use_mtime_as_document_date(self):
        fake = self.base / "이름만있는공문.hwp"
        fake.write_text("dummy", encoding="utf-8")
        os.utime(fake, None)
        meta = extract_metadata(fake.name, filename=fake.name, md_text="", full_file_path=fake)
        self.assertEqual(meta.get("request_date") or "", "")
        self.assertFalse(bool(meta.get("year")))

    def test_month_assembly_pattern_does_not_hardcode_2026(self):
        src = (SCRIPTS_DIR / "extractors" / "text_extractor.py").read_text(encoding="utf-8")
        self.assertNotIn('year or "2026"', src)
        self.assertNotIn('"year": year or "2026"', src)
        self.assertIn("datetime.date.today().year", src)

    def test_build_fts_match_two_char_and_quotes(self):
        """trigram은 3글자 미만을 색인하지 않는다. 짧은 낱말은 MATCH로 표현하지 않는다.

        예전에는 2글자 토큰을 그대로 MATCH에 넣었다. 결과는 언제나 0건이었고, 호출자는
        그 0건을 '못 찾음'으로 보고 전 행을 파이썬으로 훑었다(제안서 S2).
        2글자 검색이 **결과를 내는지**는 아래 test_two_char_query_still_finds_rows가 본다.
        """
        self.assertIn('"시정요구"', build_fts_match("시정요구"))
        self.assertIn("AND", build_fts_match("딥페이크 시정요구"))
        self.assertEqual(build_fts_match("현황"), "", "2글자는 trigram으로 표현할 수 없다")
        self.assertEqual(build_fts_match("딥페이크 피해"), "", "2글자가 섞이면 전체를 대체 경로로")
        self.assertEqual(build_fts_match("가"), "")

    def test_ledger_service_blocks_during_pipeline(self):
        db = self.base / "data_requests.db"
        js = self.base / "data_requests.json"
        conn = sqlite3.connect(str(db))
        try:
            DatabaseManager.init_schema(conn)
        finally:
            conn.close()
        guard = PipelineGuard(self.base)
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            guard.path.write_text(str(holder.pid), encoding="utf-8")
            svc = LedgerService(db_path=db, json_path=js, base_dir=self.base)
            res = svc.insert_ledger_item({
                "year": "2026", "requester": "의원", "title": "파이프라인 중 등록"
            })
            self.assertFalse(res["success"])
            self.assertIn("파이프라인", res["error"])
        finally:
            holder.kill()
            holder.wait()
            guard.release()

    def test_merge_concurrent_ledger_updates(self):
        db = self.base / "data_requests.db"
        conn = sqlite3.connect(str(db))
        try:
            DatabaseManager.init_schema(conn)
            conn.execute(
                """INSERT INTO request_ledger (
                    ledger_id, year, seq_no, party, requester, aide, title, details,
                    request_date, deadline, submit_date, department, status, note,
                    request_type, linked_doc_id, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("REQ-2026-009", "2026", "9", "", "의원", "", "웹에서 추가", "",
                 "", "", "", "", "작성중", "", "시스템", "",
                 "2026-09-10 12:00:00", "2026-09-10 12:00:00")
            )
            conn.commit()
        finally:
            conn.close()
        merged = extract_and_build_db.merge_concurrent_ledger_updates(
            [], db, "2026-09-10 11:00:00"
        )
        ids = [r["ledger_id"] for r in merged]
        self.assertIn("REQ-2026-009", ids)

    def test_gui_choseong_query_has_limit(self):
        src = (SCRIPTS_DIR / "launcher_gui.py").read_text(encoding="utf-8")
        helper = (SCRIPTS_DIR / "gui" / "search_text.py").read_text(encoding="utf-8")
        self.assertNotIn("if not is_choseong:", src)
        self.assertGreaterEqual(src.count("LIMIT 500"), 3)
        self.assertIn("build_fts_match", helper)

    def test_universal_schema_has_ledger_history(self):
        dist = self.base / "univ"
        dist.mkdir()
        (dist / "scripts").mkdir()
        build_universal_package.build_clean_database(dist)
        conn = sqlite3.connect(str(dist / "data_requests.db"))
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_history'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_bat_02_checks_errorlevel(self):
        self.assertIn("ERROR_EXIT", write_all_bats.SYSTEM_BAT_02)
        self.assertIn("if %errorlevel% neq 0 goto ERROR_EXIT", write_all_bats.SYSTEM_BAT_02)

    def test_package_exe_resolver_prefers_newer(self):
        self.assertTrue(callable(package_distribution.resolve_exe_src))
        src = (SCRIPTS_DIR / "package_distribution.py").read_text(encoding="utf-8")
        self.assertIn("st_mtime", src)
        self.assertIn("__DEPT_NAME__", src)

    def test_dashboard_search_is_same_in_offline_and_server_mode(self):
        """감사 R3-03: 02번 서버 모드도 01번과 같은 클라이언트 검색을 쓴다.

        예전 테스트는 HTTP 모드가 /api/search를 쓰는지 확인했는데, 그 경로가 초성·OR·제외어 검색을
        무력화한 원인이었다. 실제 검색 동작은 test_audit_phase10_dashboard가 Node로 실행해 검증한다.
        """
        tmpl = (SCRIPTS_DIR / "templates" / "dashboard_template.html").read_text(encoding="utf-8")
        start = tmpl.index("async function render()")
        render_head = tmpl[start:tmpl.index("document.getElementById('count-filtered')", start)]
        self.assertIn("filterItems()", render_head)
        self.assertNotIn("/api/search", render_head)
        self.assertIn("search-scope-hint", tmpl)


if __name__ == "__main__":
    unittest.main()
