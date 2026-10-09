# -*- coding: utf-8 -*-
"""코드 분할 뒤 공개 심볼·단일 HTML 전달 규칙이 깨지지 않는지 검증한다."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _symbol_inventory import (  # noqa: E402
    PACKAGE_DIRS,
    PYTHON_TARGETS,
    SNAPSHOT_PATH,
    collect_dashboard_js_names,
    collect_python_target_names,
    load_snapshot,
)
from template_renderer import DashboardRenderer  # noqa: E402

_EXTERNAL_ASSET = re.compile(r"<(?:script|link)\b[^>]+\b(?:src|href)\s*=", re.I)


class TestRefactorSplitNoLoss(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = load_snapshot()
        cls.assembled = DashboardRenderer().load_template()

    def test_snapshot_file_exists(self):
        self.assertTrue(SNAPSHOT_PATH.exists(), f"심볼 스냅샷이 없습니다: {SNAPSHOT_PATH}")

    def test_python_public_names_reexported(self):
        missing = {}
        for key, expected in self.snapshot["python"].items():
            actual = set(collect_python_target_names(key))
            lost = [name for name in expected if name not in actual]
            if lost:
                missing[key] = lost
        self.assertEqual(missing, {}, f"분할 후 사라진 Python 심볼: {json.dumps(missing, ensure_ascii=False)}")

    def test_dashboard_assembled_has_all_js_functions(self):
        expected = self.snapshot["dashboard_js"]
        actual = set(collect_dashboard_js_names(self.assembled))
        lost = [name for name in expected if name not in actual]
        self.assertEqual(lost, [], f"조립 HTML에서 빠진 JS 심볼: {lost}")

    def test_assembled_html_has_no_external_assets(self):
        self.assertIsNone(
            _EXTERNAL_ASSET.search(self.assembled),
            "조립된 대시보드에 외부 script src 또는 link href가 있습니다. 단일 파일 전달이 깨집니다.",
        )

    def test_assembled_html_keeps_placeholders_for_render(self):
        for token in ("/* __MARKED_JS__ */", "__B64_GZIP_DATA__", "__PAGE_TITLE__", "/* __APP_CONFIG_JSON__ */"):
            self.assertIn(token, self.assembled, f"템플릿 자리표시자가 없습니다: {token}")

    def test_shim_modules_export_same_objects_when_packages_exist(self):
        pairs = [
            ("services.excel_sync_service", "ExcelSyncService", "services.excel_sync"),
            ("services.ledger_service", "LedgerService", "services.ledger"),
            ("extractors.ledger_parser", "LedgerParser", "extractors.ledger"),
        ]
        for shim_mod, attr, pkg_mod in pairs:
            pkg_dir = SCRIPTS_DIR / PACKAGE_DIRS[shim_mod][0]
            if not pkg_dir.is_dir():
                continue
            shim = __import__(shim_mod, fromlist=[attr])
            try:
                pkg = __import__(pkg_mod, fromlist=[attr])
            except ImportError:
                continue
            if not hasattr(pkg, attr):
                continue
            self.assertIs(
                getattr(shim, attr),
                getattr(pkg, attr),
                f"{shim_mod}.{attr} 가 {pkg_mod}.{attr} 와 다른 객체입니다",
            )

    def test_python_target_files_still_exist_as_entrypoints(self):
        for rel in PYTHON_TARGETS.values():
            path = SCRIPTS_DIR / rel
            self.assertTrue(path.exists(), f"호환 진입점 파일이 없습니다: {rel}")


if __name__ == "__main__":
    unittest.main()
