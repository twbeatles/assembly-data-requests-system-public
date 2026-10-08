# -*- coding: utf-8 -*-
"""원문 워크스페이스 쓰기 차단 회귀 테스트.

격리 장치 이전의 테스트(`test_add_documents_smart_identical_file_skip`)가 실제 `2026`·`미분류`
원문 폴더에 25바이트짜리 가짜 HWP를 남겼고, 2026-09-15 운영 00번 실행에서 파싱 실패로 드러났다.
쓰기 가드는 시스템 폴더뿐 아니라 연도 폴더가 있는 상위 워크스페이스도 보호한다.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import write_guard
import add_documents_smart


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ws_guard_"))
        self.workspace = self.tmp / "팀 워크스페이스"
        self.system = self.workspace / "국회자료요구_스마트시스템(테스트)"
        (self.system / "tests").mkdir(parents=True)
        (self.system / "scripts").mkdir()
        (self.workspace / "2026").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workspace_year_folders_are_protected_during_tests(self):
        with mock.patch.object(write_guard, "SYSTEM_DIR", self.system):
            self.assertEqual(write_guard.protected_workspace(), self.workspace.resolve())
            self.assertTrue(write_guard.is_protected_path(self.workspace / "2026" / "문서.hwp"))
            self.assertTrue(write_guard.is_protected_path(self.workspace / "미분류" / "문서.hwp"))
            self.assertFalse(write_guard.is_protected_path(self.system / "scripts" / "a.py"))
            self.assertFalse(write_guard.is_protected_path(self.tmp / "다른 폴더" / "문서.hwp"))
            with self.assertRaises(write_guard.ProductionWriteBlocked):
                write_guard.ensure_writable(self.workspace / "2026" / "문서.hwp", "원문 폴더")

    def test_no_year_folders_means_no_workspace_protection(self):
        shutil.rmtree(self.workspace / "2026")
        with mock.patch.object(write_guard, "SYSTEM_DIR", self.system):
            self.assertIsNone(write_guard.protected_workspace())
            self.assertFalse(write_guard.is_protected_path(self.workspace / "미분류" / "문서.hwp"))

    def test_document_intake_refuses_to_copy_into_protected_workspace(self):
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        src = inbox / "가짜_요구자료.hwp"
        src.write_bytes(b"IDENTICAL_CONTENT_PAYLOAD")
        with mock.patch.object(write_guard, "SYSTEM_DIR", self.system), \
                mock.patch.object(add_documents_smart, "ROOT_DIR", self.workspace):
            with self.assertRaises(write_guard.ProductionWriteBlocked):
                add_documents_smart.copy_or_move_files([str(src)], move=False)
        self.assertFalse((self.workspace / "미분류").exists())
        self.assertEqual(list((self.workspace / "2026").iterdir()), [])


class TestIgnoredIntakeFiles(unittest.TestCase):
    """지원 외 형식 투입 파일은 조용히 버리지 않고 경고로 안내한다. (감사 R6 정리)"""

    def test_unsupported_extension_is_reported_not_silent(self):
        tmp = Path(tempfile.mkdtemp(prefix="ignored_"))
        try:
            drop = tmp / "새자료_투입폴더"
            drop.mkdir()
            (drop / "메모.txt").write_bytes(b"not a document")
            ok = {"success": True}
            with mock.patch.object(add_documents_smart, "SYSTEM_DIR", tmp), \
                    mock.patch.object(add_documents_smart, "ROOT_DIR", tmp), \
                    mock.patch.object(add_documents_smart, "DROP_DIRS", [drop]), \
                    mock.patch.object(add_documents_smart, "IS_DIST_ENV", True), \
                    mock.patch.object(add_documents_smart, "IS_DATA_DIST_ENV", True), \
                    mock.patch.object(add_documents_smart.parse_all, "main",
                                        return_value=dict(ok)), \
                    mock.patch.object(add_documents_smart.extract_and_build_db, "main",
                                        return_value=dict(ok)), \
                    mock.patch.object(add_documents_smart.generate_web_dashboard, "main",
                                        return_value=dict(ok)):
                result = add_documents_smart._run_smart_add_body()
            self.assertTrue(result["success"])
            self.assertTrue(any("지원하지 않는 형식" in w for w in result["warnings"]),
                            result["warnings"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
