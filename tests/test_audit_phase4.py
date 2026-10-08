# -*- coding: utf-8 -*-
"""
Test Suite for Audit Phase 4 Improvements.
Verifies:
1. atomic_swap retry handling and tmp_db protection on Windows locks.
2. dashboard_template offline numbering logic and SSOT template loading.
3. Excel hyperlink relative path dynamic computation.
4. ledger_service year field update permission.
5. qa_splitter deduplication guardrail (top summary vs distinct substantive questions).
6. add_documents_smart identical file skip (hash & size matching).
7. text_extractor historical date prefix (2018-2023) normalization.
"""

import os
import sys
import json
import sqlite3
import unittest
import tempfile
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db.database_manager import DatabaseManager
import generate_excel_db
import generate_web_dashboard
from services.ledger_service import LedgerService
from extractors.qa_splitter import split_into_qa_items
import add_documents_smart
from extractors.text_extractor import extract_metadata


class TestAuditPhase4(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_atomic_swap_success_and_retry(self):
        """Test atomic_swap replaces target DB normally, and handles retries without deleting tmp_db on failure."""
        target_db = self.temp_path / "target.db"
        tmp_db = self.temp_path / "tmp.db"

        # Initial creation
        with open(tmp_db, "w", encoding="utf-8") as f:
            f.write("test_content_v1")

        DatabaseManager.atomic_swap(tmp_db, target_db, max_retries=2, retry_delay=0.05)
        self.assertTrue(target_db.exists())
        self.assertEqual(target_db.read_text(encoding="utf-8"), "test_content_v1")
        self.assertFalse(tmp_db.exists())

        # Test failure case when target is permanently locked: tmp_db must be preserved!
        with open(tmp_db, "w", encoding="utf-8") as f:
            f.write("test_content_v2")

        with patch("os.replace", side_effect=PermissionError("File locked by another process")):
            with patch("shutil.copy2", side_effect=PermissionError("File locked by another process")):
                with self.assertRaises(PermissionError):
                    DatabaseManager.atomic_swap(tmp_db, target_db, max_retries=2, retry_delay=0.01)
                # Ensure tmp_db is NOT lost or deleted!
                self.assertTrue(tmp_db.exists())
                self.assertEqual(tmp_db.read_text(encoding="utf-8"), "test_content_v2")

    def test_locked_target_fails_fast_with_data_intact(self):
        """잠긴 대상에서는 무한 대기 대신 PermissionError, 커밋 데이터 보존. (ISSUE-002)

        backup()은 잠긴 대상에서 무한 대기하므로(재현됨: 25초 워치독 발동),
        별도 스레드에서 실행하고 제한 시간 안에 끝나야 한다.
        """
        target_db = self.temp_path / "target.db"
        tmp_db = self.temp_path / "tmp.db"

        setup = sqlite3.connect(str(target_db))
        setup.execute("PRAGMA journal_mode=WAL;")
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        setup.execute("INSERT INTO t VALUES (1, 'A')")
        setup.commit()
        setup.execute("INSERT INTO t VALUES (2, 'B')")
        setup.commit()
        setup.close()

        # tmp는 구버전 스냅샷(A만). 스왑 실패 후에도 B가 살아 있어야 한다.
        src = sqlite3.connect(str(tmp_db))
        src.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        src.execute("INSERT INTO t VALUES (1, 'A')")
        src.commit()
        src.close()

        # DB 파일에만 EXCLUSIVE를 건다. checkpoint 실패 + 교체 실패를 강제한다.
        blocker = sqlite3.connect(str(target_db))
        blocker.execute("BEGIN EXCLUSIVE")
        out = {}
        worker = threading.Thread(target=lambda: out.update(self._try_swap(tmp_db, target_db)), daemon=True)
        worker.start()
        worker.join(timeout=60)
        try:
            self.assertFalse(worker.is_alive(), "atomic_swap이 잠긴 대상에서 무한 대기한다")
            self.assertIn("locked", out)
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

        check = sqlite3.connect(str(target_db))
        try:
            rows = sorted(r[0] for r in check.execute("SELECT id FROM t"))
        finally:
            check.close()
        self.assertEqual(rows, [1, 2])

    @staticmethod
    def _try_swap(tmp_db, target_db):
        try:
            DatabaseManager.atomic_swap(tmp_db, target_db, max_retries=1, retry_delay=0.01)
            return {"locked": "returned"}
        except PermissionError as e:
            return {"locked": str(e)}

    def test_target_writable_probe(self):
        """교체 전 쓰기 가능 여부 확인. (ISSUE-002)"""
        db = self.temp_path / "probe.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.assertTrue(DatabaseManager._target_writable(db))
        blocker = sqlite3.connect(str(db))
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            self.assertFalse(DatabaseManager._target_writable(db))
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertTrue(DatabaseManager._target_writable(db))

    def test_dashboard_template_offline_numbering_and_ssot(self):
        """Test dashboard_template.html uses max sequence + 1 for offline numbering and SSOT template."""
        tmpl_path = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        self.assertTrue(tmpl_path.exists())
        content = tmpl_path.read_text(encoding="utf-8")

        # Must NOT use length + 1
        self.assertNotIn("REQUEST_LEDGER.length + 1", content)
        # Offline IDs must be isolated from the server's REQ sequence and later
        # replaced by a server-issued ID during synchronization.
        self.assertIn("LOCAL-${year}", content)
        self.assertIn("crypto.randomUUID", content)

        # Verify generate_web_dashboard loads from templates/
        loaded_tmpl = generate_web_dashboard.load_template()
        self.assertEqual(loaded_tmpl, content)

    def test_excel_hyperlink_relative_path(self):
        """Test resolve_excel_hyperlink_path returns correct relative formula paths."""
        # Test path in 2026 directory
        rel_2026 = generate_excel_db.resolve_excel_hyperlink_path("2026/260901_홍길동_질의서.hwp")
        self.assertIn("2026", rel_2026)
        self.assertTrue("홍길동" in rel_2026)

        # Test path inside system folder
        rel_sys = generate_excel_db.resolve_excel_hyperlink_path("국회자료요구_스마트시스템(기획예산팀)/새자료_투입폴더/test.xlsx")
        self.assertIn("test.xlsx", rel_sys)

    def test_ledger_service_year_update_allowed(self):
        """Test ledger_service allows updating the 'year' field."""
        db_file = self.temp_path / "test_ledger.db"
        json_file = self.temp_path / "test_data.json"
        
        # Setup schema
        mgr = DatabaseManager(db_file)
        conn = mgr.get_conn()
        try:
            DatabaseManager.init_schema(conn)
        finally:
            conn.close()

        service = LedgerService(db_file, json_file)
        # Add item
        add_res = service.insert_ledger_item({
            "year": "2025",
            "seq_no": "001",
            "requester": "테스트의원",
            "title": "테스트 제목"
        })
        self.assertTrue(add_res["success"])
        item_id = add_res["item"]["ledger_id"]

        # Update year to 2026
        upd_res = service.update_ledger_item(item_id, {"year": "2026", "title": "수정 제목"})
        self.assertTrue(upd_res["success"])

        # Verify DB directly
        conn = sqlite3.connect(db_file)
        try:
            row = conn.execute("SELECT year, title FROM request_ledger WHERE ledger_id = ?", (item_id,)).fetchone()
            self.assertEqual(row[0], "2026")
            self.assertEqual(row[1], "수정 제목")
        finally:
            conn.close()

        LedgerService.wait_last_sync(timeout=2.0)

    def test_qa_splitter_deduplication_guardrail(self):
        """Test qa_splitter replaces short summary table of contents, but preserves distinct substantive Q&A."""
        # Case 1: Short summary header (< 30 chars body) followed by substantive answer body
        doc_text_summary = """# 요구자료 답변서
작성일: 2026.03.01

1. 딥페이크 차단 현황
(목차 요약)

1. 딥페이크 차단 현황
기획예산팀에서는 2024년부터 2026년 현재까지 딥페이크 성범죄 영상물 35,000건을 긴급 심의하여 98% 이상 삭제 및 접속차단 조치를 완료하였습니다.
"""
        meta = {"year": "2026", "requester": "김의원", "title": "딥페이크 현황"}
        items1 = split_into_qa_items("DOC-01", meta, doc_text_summary)
        # Should deduplicate the short table of contents header into 1 item
        self.assertEqual(len(items1), 1)
        self.assertIn("35,000건", items1[0]["answer_full"])

        # Case 2: Two distinct sections with same title but both having substantive answers (> 30 chars body)
        doc_text_distinct = """# 종합 질의서

1. 조치 사항
경찰청 및 상급감독기관와의 공조 체계를 바탕으로 24시간 상시 모니터링 시스템을 운영하고 있으며, 주요 해외 플랫폼 사업자와의 핫라인을 통해 신속 대응하고 있습니다.

2. 조치 사항
불법 촬영물 유포 피해자에 대한 2차 피해 방지를 위하여 상담 지원 센터와 연계하여 심리 상담 및 법률 지원 서비스를 제공하고 있습니다.
"""
        items2 = split_into_qa_items("DOC-02", meta, doc_text_distinct)
        # Both must be preserved because both have substantive content!
        self.assertEqual(len(items2), 2)
        self.assertIn("24시간 상시 모니터링", items2[0]["answer_full"])
        self.assertIn("심리 상담 및 법률 지원", items2[1]["answer_full"])

    def test_add_documents_smart_identical_file_skip(self):
        """Test copy_or_move_files skips re-copying or appending timestamps when identical file exists."""
        original_root = add_documents_smart.ROOT_DIR
        add_documents_smart.ROOT_DIR = self.temp_path
        target_dir = self.temp_path / "2026"
        target_dir.mkdir(parents=True, exist_ok=True)
        existing_file = target_dir / "test_audit_phase4_dup.hwp"
        existing_file.write_text("IDENTICAL_CONTENT_PAYLOAD", encoding="utf-8")

        # Source file with identical content in another temp location
        src_file = self.temp_path / "test_audit_phase4_dup.hwp"
        src_file.write_text("IDENTICAL_CONTENT_PAYLOAD", encoding="utf-8")

        try:
            # When move=False, identical file must not create a timestamped duplicate
            processed = add_documents_smart.copy_or_move_files([str(src_file)], move=False)
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0].name, "test_audit_phase4_dup.hwp")

            # Check that no timestamped file was generated in target_dir
            timestamped = list(target_dir.glob("test_audit_phase4_dup_*.hwp"))
            self.assertEqual(len(timestamped), 0)
        finally:
            existing_file.unlink(missing_ok=True)
            add_documents_smart.ROOT_DIR = original_root

    def test_text_extractor_date_prefix_regex(self):
        """Test title normalization correctly removes 2018-2023 historical date prefixes."""
        # 2021 date prefix
        res1 = extract_metadata(r"2021\20210515_대외기관_불법촬영물_조치현황.hwp")
        self.assertEqual(res1["title"], "대외기관_불법촬영물_조치현황")

        # 2-digit 19 date prefix (2019)
        res2 = extract_metadata(r"2019\190820_국회_정보통신망_현황.hwp")
        self.assertEqual(res2["title"], "국회_정보통신망_현황")

        # 2026 date prefix
        res3 = extract_metadata(r"2026\260309_국회_질의서.hwpx")
        self.assertEqual(res3["title"], "국회_질의서")


if __name__ == "__main__":
    unittest.main()
