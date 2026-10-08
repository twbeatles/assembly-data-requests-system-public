# -*- coding: utf-8 -*-
"""대시보드 UI/UX 정리(2026-10-06) 회귀 테스트.

브라우저에서 재현한 흐름 문제를 다시 만들지 않게 한다. 소스 grep이 아니라
shell.html은 HTML 파서로, JS는 Node에서 실제로 실행해 확인한다. 운영 데이터는 건드리지 않는다.
"""
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from test_audit_phase10_dashboard import run_node

REPO = Path(__file__).resolve().parent.parent
DASH = REPO / "scripts" / "templates" / "dashboard"
SHELL = DASH / "shell.html"
JS_DIR = DASH / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.inline_styles = []
        self.tab_keys = []
        self.sort_values = []
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        style = (a.get("style") or "").replace(" ", "").rstrip(";")
        # JS가 켜고 끄는 display:none만 허용한다. 모양은 styles.css의 클래스로만 정한다.
        if style and style != "display:none":
            self.inline_styles.append((tag, a.get("id"), a.get("style")))
        if tag == "button" and "modal-tab-btn" in (a.get("class") or ""):
            self.tab_keys.append(a.get("data-tab"))
        if tag == "select":
            self._select = a.get("id")
        if tag == "option" and self._select == "sel-sort":
            self.sort_values.append(a.get("value"))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def _scan():
    scan = _Scan()
    scan.feed(SHELL.read_text(encoding="utf-8"))
    return scan


def test_shell_has_refactor_hooks():
    scan = _scan()
    needed = {"btn-mark-submitted", "ledger-detail-due", "select-all-wrap", "search-clear",
              "empty-clear-filters", "link-excel-db", "search-scope-hint"}
    assert not (needed - scan.ids), sorted(needed - scan.ids)


def test_shell_has_no_inline_styling():
    scan = _scan()
    assert scan.inline_styles == [], f"shell.html에 인라인 style이 남아 있다: {scan.inline_styles[:5]}"


def test_modal_tabs_are_keyed_by_data_tab():
    scan = _scan()
    assert sorted(scan.tab_keys) == ["formatted", "qa", "text"], scan.tab_keys


@needs_node
def test_sort_options_match_mode_table():
    scan = _scan()
    res = run_node(["MODE_SORTS"], "console.log(JSON.stringify(MODE_SORTS));")
    assert set(res) == {"docs", "tables", "qa", "ledger"}
    for mode, sorts in res.items():
        assert set(sorts) <= set(scan.sort_values), (mode, sorts)
    assert "deadline-asc" in res["ledger"] and "deadline-asc" not in res["docs"]
    assert "qa-count" not in res["ledger"]


@needs_node
def test_status_label_and_question_count_sort():
    res = run_node(
        ["LEDGER_DONE_STATUSES", "LEDGER_PROGRESS_STATUSES", "ledgerStatusLabel", "ledgerStatusClass",
         "sortSearchResults"],
        """
const DOC_QAS_MAP = { A: [1], B: [1, 2, 3], C: [1, 2] };
activeFilters.sort = 'qa-count';
const sorted = sortSearchResults([
  { doc_id: 'A', table_count: 9 }, { doc_id: 'B', table_count: 0 }, { doc_id: 'C', table_count: 5 }
]).map(x => x.doc_id);
console.log(JSON.stringify({
  sorted,
  empty: ledgerStatusLabel(''),
  cls: ['', '제출', '업무설명', '검토중', '해당없음', '미제출'].map(ledgerStatusClass),
}));
""")
    # 예전에는 표 개수(table_count)로 정렬해 A, C, B가 나왔다.
    assert res["sorted"] == ["B", "C", "A"]
    # 빈 진행 상태는 엑셀 규칙(불변조건 24)과 같이 '미제출'이다. 예전 화면은 '제출'로 보여 줬다.
    assert res["empty"] == "미제출"
    assert res["cls"] == ["todo", "done", "done", "progress", "na", "todo"]


@needs_node
def test_card_preview_strips_markdown_only_for_preview():
    res = run_node(["cardPreviewText"], r"""
const md = '<!-- source: a.hwp -->\n# 답변서\n\n## 1. 현황\n\n| 연도 | 건수 |\n| --- | --- |\n| 2024 | **10** |\n';
console.log(JSON.stringify({ out: cardPreviewText(md), same: md.includes('| --- |') }));
""")
    assert res["out"] == "답변서 1. 현황 연도 건수 2024 10"
    assert res["same"] is True, "원문 문자열은 바꾸지 않는다(전문 보존)"


@needs_node
def test_saved_form_closes_without_discard_prompt(tmp_path):
    """저장 직후 닫을 때 '저장하지 않고 닫을까요?'를 묻지 않는다. 사용자가 닫을 때만 묻는다."""
    assert NODE is not None
    driver = tmp_path / "close_driver.cjs"
    driver.write_text(
        "const fs = require('fs');\n"
        "const src = fs.readFileSync(process.argv[2], 'utf-8');\n"
        "const g = globalThis;\n"
        "g.__confirms = 0; g.__closed = 0;\n"
        "g.confirm = () => { g.__confirms += 1; return false; };\n"
        "g.document = { readyState: 'complete', activeElement: null, addEventListener() {},\n"
        "  querySelectorAll: () => [], getElementById: () => ({ value: '적어 둔 내용', classList: { contains: () => false } }) };\n"
        "g.closeAddLedgerModal = function () { g.__closed += 1; };\n"
        "(0, eval)(src);\n"
        "g.closeAddLedgerModal();\n"
        "const afterUserClose = [g.__confirms, g.__closed];\n"
        "g.closeAddLedgerModal(true);\n"
        "console.log(JSON.stringify({ afterUserClose, afterSavedClose: [g.__confirms, g.__closed] }));\n",
        encoding="utf-8")
    run = subprocess.run([NODE, str(driver), str(JS_DIR / "15_ux.js")], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr[:800]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["afterUserClose"] == [1, 0], "적어 둔 내용이 있으면 사용자가 닫을 때는 물어야 한다"
    assert out["afterSavedClose"] == [1, 1], "저장 뒤(true)에는 묻지 않고 닫아야 한다"


def test_doc_modal_does_not_assign_location_hash():
    """location.hash 대입은 hashchange로 문서를 한 번 더 열어 '질문별로 보기' 탭을 되돌렸다."""
    src = (JS_DIR / "12_modal.js").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("//")]
    assert not any("location.hash =" in ln for ln in code_lines)


def test_app_config_json_carries_excel_filename():
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    from template_renderer import DashboardRenderer
    cfg = json.loads(DashboardRenderer.app_config_json(
        {"department_name": "부서", "excel_filename": "국회_기관_자료요구_통합DB.xlsx"}))
    assert cfg["excel_filename"] == "국회_기관_자료요구_통합DB.xlsx"
