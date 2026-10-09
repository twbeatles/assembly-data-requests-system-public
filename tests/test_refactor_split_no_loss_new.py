# -*- coding: utf-8 -*-
"""2차 코드 분할(2026-09) 뒤 공개 심볼·동일 객체가 깨지지 않는지 검증한다.

분할 전 스냅샷(tests/_expected_symbols_new.json)과
진입점+패키지 합집합을 비교한다. 소스 grep이 아니라 ast 선언 수집이다.
"""
from __future__ import annotations
from typing import Any

import builtins
import json
import shutil
import sqlite3
import symtable
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _symbol_inventory import (  # noqa: E402
    PACKAGE_DIRS,
    PYTHON_TARGETS,
    collect_python_target_names,
)

NEW_SNAPSHOT_PATH = TESTS_DIR / "_expected_symbols_new.json"

NEW_KEYS = (
    "build_universal_package",
    "generate_excel_db",
    "extract_and_build_db",
    "write_all_bats",
    "package_distribution",
    "add_documents_smart",
    "search_query",
    "template_renderer",
)

IDENTITY_PAIRS = (
    ("web_server", "paged_response", "web.pagination", "paged_response"),
    ("parse_all", "needs_safe_copy", "pipeline.parse.safe_copy", "needs_safe_copy"),
    ("parse_all", "handle_large_xlsx_fallback", "pipeline.parse.xlsx_fallback", "handle_large_xlsx_fallback"),
    ("search_query", "parse_query", "search.parse", "parse_query"),
    ("search_query", "row_matches", "search.match", "row_matches"),
    ("generate_excel_db", "build_ledger_sheet", "excel_report.sheets", "build_ledger_sheet"),
    ("extract_and_build_db", "assign_doc_id", "build_db.docids", "assign_doc_id"),
    ("write_all_bats", "validate_batch_file_rules", "bats.validate", "validate_batch_file_rules"),
    ("build_universal_package", "build_clean_database", "universal_pkg.database", "build_clean_database"),
    ("add_documents_smart", "copy_or_move_files", "ingest.files", "copy_or_move_files"),
)


class TestRefactorSplitNoLossNew(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads(NEW_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    def test_new_snapshot_file_exists(self):
        self.assertTrue(NEW_SNAPSHOT_PATH.exists(), f"심볼 스냅샷이 없습니다: {NEW_SNAPSHOT_PATH}")

    def test_new_targets_registered_in_inventory(self):
        for key in NEW_KEYS:
            self.assertIn(key, PYTHON_TARGETS, f"인벤토리에 미등록: {key}")
            self.assertIn(key, PACKAGE_DIRS, f"패키지 매핑 미등록: {key}")

    def test_new_python_public_names_reexported(self):
        missing = {}
        for key in NEW_KEYS:
            expected = self.snapshot[PYTHON_TARGETS[key]]
            actual = set(collect_python_target_names(key))
            lost = [name for name in expected if name not in actual]
            if lost:
                missing[key] = lost
        self.assertEqual(missing, {}, f"분할 후 사라진 Python 심볼: {json.dumps(missing, ensure_ascii=False)}")

    def test_new_shim_modules_export_same_objects(self):
        for shim_mod, attr, pkg_mod, pkg_attr in IDENTITY_PAIRS:
            with self.subTest(shim=shim_mod, attr=attr):
                shim = __import__(shim_mod, fromlist=[attr])
                pkg = __import__(pkg_mod, fromlist=[pkg_attr])
                self.assertIs(
                    getattr(shim, attr),
                    getattr(pkg, pkg_attr),
                    f"{shim_mod}.{attr} 가 {pkg_mod}.{pkg_attr} 와 다른 객체입니다",
                )

    def test_new_target_files_still_exist_as_entrypoints(self):
        for key in NEW_KEYS:
            path = SCRIPTS_DIR / PYTHON_TARGETS[key]
            self.assertTrue(path.exists(), f"호환 진입점 파일이 없습니다: {PYTHON_TARGETS[key]}")


# 분할 때 import가 빠져나간 이름은 NameError로 터진다. 심볼 재수출 검사가
# 통과해도 호출자 쪽 import가 빠지면 못 잡으므로, 이름 해결까지 본다. (R6-06~08)
_PROVIDED_NAMES = frozenset({
    "__file__", "__name__", "__doc__", "__package__", "__loader__",
    "__spec__", "__cached__", "__builtins__",
    "__annotate__", "__classdict__",  # 변수 어노테이션용 컴파일러 생성 이름
})
_BUILTIN_NAMES = frozenset(dir(builtins))


def _undefined_names(path: Path) -> list:
    """미정의 bare 이름 목록. 인터프리터 제공 이름은 제외한다.

    grep이 아니라 symtable 수준 검사다. bare 이름 사용이 import·대입·인자로
    해결되지 않으면 분할 누락이다.
    """
    top = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    found = []

    def defined_here(table):
        names = set()
        for ident in table.get_identifiers():
            try:
                sym = table.lookup(ident)
            except KeyError:
                continue
            if (sym.is_assigned() or sym.is_imported() or sym.is_parameter()
                    or sym.is_namespace() or sym.is_declared_global()):
                names.add(ident)
        return names

    def visit(table, visible, in_function):
        now = visible | defined_here(table)
        for ident in table.get_identifiers():
            if ident in _PROVIDED_NAMES or ident in _BUILTIN_NAMES:
                continue
            if ident == "__class__" and in_function:
                continue  # zero-arg super()용 컴파일러 셀
            try:
                sym = table.lookup(ident)
            except KeyError:
                continue
            if sym.is_referenced() and ident not in now:
                found.append(f"{table.get_name()}:{ident}")
        for child in table.get_children():
            visit(child, now, in_function or child.get_type() == "function")

    visit(top, frozenset(), False)
    return sorted(set(found))


class TestSplitImportsResolve(unittest.TestCase):
    """분할 패키지의 이름 해결. `scrolledtext`(기동 크래시), `tk`(복사 크래시),

    `paged_response`(문서·Q&A·대장 API 500)가 분할 때 빠져나갔다. (R6-06~08)
    """

    def test_split_files_have_no_undefined_names(self):
        bad = {}
        for path in sorted(SCRIPTS_DIR.rglob("*.py")):
            names = _undefined_names(path)
            if names:
                bad[path.relative_to(SCRIPTS_DIR).as_posix()] = names
        self.assertEqual(bad, {}, f"미정의 이름: {json.dumps(bad, ensure_ascii=False)}")

    def test_api_ledger_route_returns_paged_body(self):
        """GET /api/ledger가 분할된 페이징 헬퍼로 200+페이징 본문을 돌려준다.

        `routes_get`가 `paged_response` import를 잃으면 NameError→500이 된다.
        핸들러 실물(`__new__` + 경로 지정, phase5와 같은 방식)로 확인한다.
        """
        import web_server
        from db.database_manager import DatabaseManager
        from web_server import RequestLedgerHandler
        tmp = Path(tempfile.mkdtemp(prefix="split_route_"))
        try:
            db = tmp / "data_requests.db"
            conn = sqlite3.connect(str(db))
            try:
                DatabaseManager.ensure_schema(conn)
                conn.execute(
                    "INSERT INTO request_ledger (ledger_id, year, title, requester, status)"
                    " VALUES (?, ?, ?, ?, ?)",
                    ("REQ-2026-001", "2026", "분할 테스트", "의원", "작성중"))
                conn.commit()
            finally:
                conn.close()
            handler: Any = RequestLedgerHandler.__new__(RequestLedgerHandler)
            handler.headers = {"Origin": "http://localhost:8000"}
            sent = []
            handler.send_json_response = (
                lambda data, status_code=200: sent.append((status_code, data)))
            handler.send_error_response = (
                lambda code, msg: sent.append((code, {"success": False, "error": msg})))
            handler.path = "/api/ledger"
            with mock.patch.object(web_server, "BASE_DIR", tmp), \
                    mock.patch.object(web_server, "DB_PATH", db), \
                    mock.patch.object(web_server, "JSON_PATH",
                                        tmp / "data_requests.json"):
                handler.do_GET()
            self.assertEqual(len(sent), 1, sent)
            code, body = sent[0]
            self.assertEqual(code, 200, body)
            self.assertTrue(body["success"])
            self.assertGreaterEqual(body["total"], 1)
            self.assertTrue(any(r["ledger_id"] == "REQ-2026-001"
                                for r in body["data"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
