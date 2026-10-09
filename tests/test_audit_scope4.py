# -*- coding: utf-8 -*-
"""SCOPE4 감사 개선 회귀 테스트."""

import hashlib
import sqlite3
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract_and_build_db
import add_documents_smart
import generate_excel_db
import write_all_bats
from extractors.text_extractor import extract_metadata
from set_department import validate_label
from db.database_manager import DatabaseManager


class TestAuditScope4(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_doc_id_undated_not_2026(self):
        did = extract_and_build_db.assign_doc_id({}, {}, "a.md", "a.hwp", None, "")
        self.assertTrue(did.startswith("DOC-UNDATED-"))
        self.assertNotIn("2026", did)

    def test_doc_id_stable_after_path_change(self):
        src = self.base / "orig.hwp"
        src.write_bytes(b"same-bytes-content")
        h = extract_and_build_db.file_sha256(src)
        existing = {"sha256:" + h: "DOC-2023-007"}
        moved = self.base / "미분류" / "orig.hwp"
        moved.parent.mkdir()
        shutil.copy2(src, moved)
        did = extract_and_build_db.assign_doc_id(
            existing, {}, "_parsed_markdown/미분류/orig.hwp.md", "미분류/orig.hwp", moved, "2023"
        )
        self.assertEqual(did, "DOC-2023-007")

    def test_doc_id_identical_content_gets_unique_ids(self):
        """동일한 내용(동일 sha256)의 복사본 파일들이 각각 고유한 doc_id를 발급받는지 검증."""
        src1 = self.base / "file1.hwp"
        src1.write_bytes(b"duplicate-content-payload")
        src2 = self.base / "file2.hwp"
        src2.write_bytes(b"duplicate-content-payload")
        
        assigned = set()
        existing_map = {}
        max_ids = {}

        did1 = extract_and_build_db.assign_doc_id(
            existing_map, max_ids, "_parsed/f1.md", "2024/f1.hwp", src1, "2024", assigned_ids=assigned
        )
        did2 = extract_and_build_db.assign_doc_id(
            existing_map, max_ids, "_parsed/f2.md", "2024/f2.hwp", src2, "2024", assigned_ids=assigned
        )

        self.assertNotEqual(did1, did2, "동일한 내용의 파일이라도 고유한 doc_id를 가져야 합니다.")
        self.assertEqual(len(assigned), 2)
        self.assertIn(did1, assigned)
        self.assertIn(did2, assigned)

    def test_doc_id_does_not_reuse_already_assigned(self):
        """후보 doc_id가 이미 이번 배치에서 할당된 경우 중복 사용하지 않고 신규 ID 발급."""
        existing = {"sha256:somehash": "DOC-2024-001"}
        assigned = {"DOC-2024-001"}
        max_ids = {"2024": 1}
        did = extract_and_build_db.assign_doc_id(
            existing, max_ids, "_parsed/new.md", "2024/new.hwp", None, "2024", assigned_ids=assigned
        )
        self.assertNotEqual(did, "DOC-2024-001")
        self.assertEqual(did, "DOC-2024-002")

    def test_load_existing_id_map_from_sqlite(self):
        db = self.base / "data_requests.db"
        conn = sqlite3.connect(str(db))
        try:
            DatabaseManager.init_schema(conn)
            conn.execute(
                """INSERT INTO documents (
                    doc_id, year, request_date, institution, requester, doc_number, version, title,
                    department, contact_person, question_list, answer_summary, answer_full_text,
                    has_tables, table_count, table_summary, topic_tags, parsed_md_path, original_path,
                    full_markdown, linked_ledger_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("DOC-2022-003", "2022", "", "국회", "의원", "", "v1", "제목",
                 "", "", "", "", "", "N", 0, "", "", "p.md", "2022/a.hwp", "", "")
            )
            conn.commit()
        finally:
            conn.close()
        mapping, maxes = extract_and_build_db.load_existing_id_map(
            self.base / "missing.json", db, self.base
        )
        self.assertEqual(mapping.get("2022/a.hwp"), "DOC-2022-003")
        self.assertEqual(maxes.get("2022"), 3)

    def test_generate_excel_db_uses_temp_replace(self):
        src = (SCRIPTS_DIR / "generate_excel_db.py").read_text(encoding="utf-8")
        self.assertIn("def save_workbook_atomic", src)
        self.assertIn(".tmp.xlsx", src)
        self.assertIn("os.replace", src)

    def test_requester_committee_substring_not_oversight(self):
        meta = extract_metadata(
            "붙임1_심의 관련 부서별 담당 정보.hwp",
            filename="붙임1_심의 관련 부서별 담당 정보.hwp",
            md_text=""
        )
        self.assertNotEqual(meta["requester"], "대외위원회")

    def test_requester_calculation_filename_not_internal_stats(self):
        meta = extract_metadata("예산 계산 참고.xlsx", filename="예산 계산 참고.xlsx", md_text="")
        self.assertNotEqual(meta["requester"], "전담팀 자체통계")

    def test_copy_collision_unique_names(self):
        orig_root = add_documents_smart.ROOT_DIR
        orig_drop = add_documents_smart.DROP_DIRS
        try:
            add_documents_smart.ROOT_DIR = self.base
            drop = self.base / "새자료_투입폴더"
            drop.mkdir()
            add_documents_smart.DROP_DIRS = [drop]
            year_dir = self.base / "2025"
            year_dir.mkdir()
            existing = year_dir / "250101_같은이름.hwp"
            existing.write_bytes(b"content-aaa")
            src1 = drop / "250101_같은이름.hwp"
            src1.write_bytes(b"content-bbb-different")
            moved = add_documents_smart.copy_or_move_files([src1], move=True)
            self.assertEqual(len(moved), 1)
            self.assertTrue(moved[0].exists())
            self.assertNotEqual(moved[0].name, "250101_같은이름.hwp")
            self.assertTrue(existing.exists())
        finally:
            add_documents_smart.ROOT_DIR = orig_root
            add_documents_smart.DROP_DIRS = orig_drop

    def test_set_department_rejects_html_in_name(self):
        with self.assertRaises(ValueError):
            validate_label("<script>alert(1)</script>", "부서명")
        with self.assertRaises(ValueError):
            validate_label("__B64_GZIP_DATA__", "부서명")
        self.assertEqual(validate_label("기획예산팀", "부서명"), "기획예산팀")

    def test_dashboard_has_hashchange_listener(self):
        tmpl = (SCRIPTS_DIR / "templates" / "dashboard_template.html").read_text(encoding="utf-8")
        self.assertIn("hashchange", tmpl)
        self.assertIn("applyDeepLink", tmpl)
        self.assertIn("decodeURIComponent", tmpl)

    def test_open_excel_bat_avoids_glob_and_prefers_waiting(self):
        self.assertIn("*_최신동기화_대기.xlsx", write_all_bats.SYSTEM_BAT_03)
        self.assertNotIn("*통합DB*.xlsx", write_all_bats.SYSTEM_BAT_03)
        self.assertIn("SET_DEPT_FAIL", write_all_bats.SYSTEM_BAT_04)


if __name__ == "__main__":
    unittest.main()
