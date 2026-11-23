# -*- coding: utf-8 -*-
"""PROJECT_AUDIT_SECURITY.md 후속 조치 회귀 테스트 (SEC-001/SEC-002).

- 엑셀 쓰기는 수식 시작 값을 글자로 무력화하고, 읽기는 복원한다.
- 운영 데이터에 쓰지 않는다. 모두 임시 폴더·메모리 워크북으로 검증한다.
"""

import shutil
import tempfile
import unittest
from pathlib import Path


class ExcelFormulaNeutralizeTests(unittest.TestCase):
    def test_neutralize_prefixes_formula_starters(self):
        from extractors.ledger.workbook import neutralize_excel_value

        self.assertEqual(neutralize_excel_value("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(neutralize_excel_value("+alpha"), "'+alpha")
        self.assertEqual(neutralize_excel_value("-긴급"), "'-긴급")
        self.assertEqual(neutralize_excel_value("@mention"), "'@mention")

    def test_neutralize_leaves_normal_values_alone(self):
        from extractors.ledger.workbook import neutralize_excel_value

        self.assertEqual(neutralize_excel_value("국회 요구자료"), "국회 요구자료")
        self.assertEqual(neutralize_excel_value(""), "")
        self.assertEqual(neutralize_excel_value("'이미접두"), "'이미접두")
        self.assertEqual(neutralize_excel_value(123), 123)
        self.assertIsNone(neutralize_excel_value(None))

    def test_deneutralize_restores_only_own_prefix(self):
        from extractors.ledger.workbook import deneutralize_excel_value

        self.assertEqual(deneutralize_excel_value("'=1+1"), "=1+1")
        self.assertEqual(deneutralize_excel_value("'-긴급"), "-긴급")
        self.assertEqual(deneutralize_excel_value("국회 요구자료"), "국회 요구자료")
        self.assertEqual(deneutralize_excel_value("'일반"), "'일반")
        self.assertEqual(deneutralize_excel_value("'"), "'")
        self.assertIsNone(deneutralize_excel_value(None))

    def test_roundtrip_through_real_workbook(self):
        """무력화 저장 → data_only 재독 → 복원 후 원본과 일치한다.

        무력화하지 않으면 openpyxl이 수식(`data_type='f'`)으로 저장하고
        `data_only` 읽기가 `None`을 돌려 DB 값을 지운다. (SEC-001 실측)
        """
        import openpyxl
        from extractors.ledger.workbook import (
            deneutralize_excel_value,
            neutralize_excel_value,
            save_workbook_atomic,
        )

        tmp = Path(tempfile.mkdtemp(prefix="sec_formula_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        originals = ["=HYPERLINK(1)", "-긴급", "+alpha", "@x", "보통 제목"]
        wb = openpyxl.Workbook()
        ws = wb.active
        for i, v in enumerate(originals, start=1):
            ws.cell(row=i, column=1, value=neutralize_excel_value(v))
        target = tmp / "m.xlsx"
        self.assertTrue(save_workbook_atomic(wb, target, backup=False))

        reread = openpyxl.load_workbook(str(target))
        try:
            for i in range(1, len(originals) + 1):
                self.assertEqual(reread.active.cell(i, 1).data_type, "s")
        finally:
            reread.close()

        cached = openpyxl.load_workbook(str(target), data_only=True)
        try:
            restored = [
                deneutralize_excel_value(cached.active.cell(i, 1).value)
                for i in range(1, len(originals) + 1)
            ]
        finally:
            cached.close()
        self.assertEqual(restored, originals)

    def test_unneutralized_value_becomes_formula(self):
        """무력화 없이는 수식으로 저장된다는 전제를 고정한다."""
        import openpyxl

        wb = openpyxl.Workbook()
        wb.active["A1"] = "=1+1"
        tmp = Path(tempfile.mkdtemp(prefix="sec_formula_raw_"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        p = tmp / "r.xlsx"
        wb.save(p)
        wb.close()
        reread = openpyxl.load_workbook(str(p), data_only=True)
        try:
            self.assertIsNone(reread.active["A1"].value)
        finally:
            reread.close()


class RowIdentityRestoreTests(unittest.TestCase):
    def test_row_identity_restores_neutralized_title(self):
        """지문 대조용 읽기에서 접두를 복원해야 거짓 conflict가 없다."""
        from types import SimpleNamespace

        import openpyxl
        from extractors.ledger.dates import LEDGER_COLUMN_ALIASES
        from extractors.ledger.workbook import neutralize_excel_value
        from services.excel_sync.apply import ExcelApplyMixin
        from services.excel_sync.identity import ROW_IDENTITY_FIELDS, identity_matches

        wb = openpyxl.Workbook()
        ws = wb.active
        headers = [names[0] for _field, names in LEDGER_COLUMN_ALIASES]
        for c, h in enumerate(headers, start=1):
            ws.cell(row=1, column=c, value=h)
        title = "-긴급 요구자료"
        header_map = {h: c + 1 for c, h in enumerate(headers)}
        from extractors.ledger.dates import TITLE_HEADER_NAMES

        ws.cell(
            row=2,
            column=header_map[TITLE_HEADER_NAMES[0]],
            value=neutralize_excel_value(title),
        )
        host = SimpleNamespace(
            _resolve_columns=lambda ws: ExcelApplyMixin._resolve_columns(ws)
        )
        row = ExcelApplyMixin._row_identity(host, wb, (ws.title, 2))
        self.assertIn("title", ROW_IDENTITY_FIELDS)
        self.assertTrue(
            identity_matches(
                row, {"title": title, "requester": "", "aide": "", "seq_no": ""}
            )
        )


class SanitizeHyperlinkTests(unittest.TestCase):
    def test_sanitize_text_neutralizes_formula_start(self):
        from excel_report.text import sanitize_text

        self.assertEqual(sanitize_text("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(sanitize_text("-긴급"), "'-긴급")
        dirty = "ok\x00text"
        self.assertNotIn("\x00", sanitize_text(dirty))

    def test_hyperlink_literal_doubles_quotes(self):
        from excel_report.links import hyperlink_literal

        self.assertEqual(hyperlink_literal('a"b'), 'a""b')
        self.assertEqual(hyperlink_literal("REQ-2026-001"), "REQ-2026-001")


class DependencyPinTests(unittest.TestCase):
    def test_openpyxl_is_pinned(self):
        root = Path(__file__).resolve().parent.parent
        line = (root / "requirements.txt").read_text(encoding="utf-8").strip()
        self.assertTrue(
            line.startswith("openpyxl=="),
            "수식 저장 동작을 검증한 버전으로 고정해야 한다. (SEC-002)",
        )


if __name__ == "__main__":
    unittest.main()
