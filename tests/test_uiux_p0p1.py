# -*- coding: utf-8 -*-
"""UI/UX P0·P1 회귀 테스트 (기능_UIUX_개선_제안서 1·2단계).

소스 grep이 아니라 실제 파싱·실행으로 검증한다: shell.html은 HTML 파서로,
대시보드 JS는 node --check와 순수 함수 실행 하네스로 확인한다.
운영 데이터에 쓰지 않는다(읽기만 한다).
"""
import datetime
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL = REPO / "scripts" / "templates" / "dashboard" / "shell.html"
JS_DIR = REPO / "scripts" / "templates" / "dashboard" / "js"
TEMPLATE = REPO / "scripts" / "templates" / "dashboard_template.html"
MANIFEST = REPO / "scripts" / "templates" / "dashboard" / "manifest.json"

REQUIRED_IDS = {
    "conn-status", "due-summary", "due-num-overdue", "due-num-soon", "due-num-open",
    "tab-count-docs", "tab-count-qa", "tab-count-tables", "tab-count-ledger",
    "grammar-chips", "topic-chips", "active-filter-chips", "search-spinner",
    "add-ledger-sub", "add-ledger-title", "edit-ledger-title",
    "similar-answers", "similar-list", "similar-count",
    "ledger-history-box", "ledger-history-list", "ledger-history-count", "ledger-history-offline",
    "ledger-link-select", "btn-save-ledger-link", "btn-clear-ledger-link",
    "requester-candidates",
    "add-req-date-preview", "add-deadline-preview", "add-submit-date-preview",
    "edit-req-date-preview", "edit-deadline-preview", "edit-submit-date-preview",
}

DIALOG_IDS = {
    "add-ledger-backdrop", "edit-ledger-backdrop", "ledger-modal-backdrop",
    "modal-backdrop", "compare-backdrop", "quarantine-modal-backdrop",
    "config-modal-backdrop",
}


class _ShellScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.dialog_roles = {}
        self.close_missing_aria = 0
        self.add_year_value = "MISSING"
        self.select_options = {}
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("id") in DIALOG_IDS:
            self.dialog_roles[a["id"]] = (a.get("role"), a.get("aria-modal"))
        if tag == "button" and "btn-close" in (a.get("class") or ""):
            if not a.get("aria-label"):
                self.close_missing_aria += 1
        if tag == "input" and a.get("id") == "add-year":
            self.add_year_value = a.get("value")
        if tag == "select":
            self._select = a.get("id")
            if self._select in ("add-status", "edit-status"):
                self.select_options.setdefault(self._select, [])
        if tag == "option" and self._select in ("add-status", "edit-status"):
            self.select_options[self._select].append(a.get("value"))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def _scan_shell():
    scan = _ShellScan()
    scan.feed(SHELL.read_text(encoding="utf-8"))
    return scan


def test_shell_has_p0p1_hooks():
    scan = _scan_shell()
    missing = REQUIRED_IDS - scan.ids
    assert not missing, f"shell.html에 없는 P0/P1 요소: {sorted(missing)}"


def test_year_not_hardcoded():
    scan = _scan_shell()
    assert scan.add_year_value is None, "등록 폼 연도에 하드코딩 값이 남아 있다"


def test_status_options_unified():
    scan = _scan_shell()
    assert scan.select_options["add-status"] == scan.select_options["edit-status"], (
        f"등록/수정 상태 선택지가 다르다: {scan.select_options}")
    assert set(scan.select_options["add-status"]) >= {
        "작성중", "검토중", "제출", "완료", "업무설명", "미제출", "해당없음"}


def test_dialog_a11y():
    scan = _scan_shell()
    for dlg in DIALOG_IDS:
        role, modal = scan.dialog_roles.get(dlg, (None, None))
        assert role == "dialog" and modal == "true", f"{dlg}에 role/aria-modal 없음"
    assert scan.close_missing_aria == 0, "닫기 버튼에 aria-label 없음"


def test_no_alert_in_dashboard_js():
    bad = []
    for path in sorted(JS_DIR.glob("*.js")):
        if "alert(" in path.read_text(encoding="utf-8"):
            bad.append(path.name)
    assert not bad, f"alert() 잔존 (notify로 교체해야): {bad}"


def test_manifest_includes_ux_part():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "js/15_ux.js" in manifest["js"]
    assert (JS_DIR / "15_ux.js").exists()


def test_assembled_template_has_ux_and_no_alert():
    html = TEMPLATE.read_text(encoding="utf-8")
    for marker in ("closeTopModal", "due-summary", "ledger-history-list",
                   "requester-candidates", "refreshSearchExtras"):
        assert marker in html, f"조립 캐시에 {marker} 없음 (write_assembled_template 필요)"
    assert "__DASHBOARD_JS__" not in html and "__DASHBOARD_CSS__" not in html
    assert "alert(" not in html
    assert "<script src" not in html and "<link href" not in html


def test_dashboard_js_syntax():
    for path in sorted(JS_DIR.glob("*.js")):
        res = subprocess.run(
            ["node", "--check", str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
        assert res.returncode == 0, f"{path.name} 구문 오류: {res.stderr[:500]}"


def test_ux_pure_functions_run_in_node(tmp_path):
    driver = tmp_path / "ux_driver.cjs"
    driver.write_text(
        "const fs = require('fs');\n"
        "const src = fs.readFileSync(process.argv[2], 'utf-8');\n"
        "const code = src + `\n;globalThis.__results = {\n"
        "  p1: previewLedgerDate('2026.9.10'),\n"
        "  p2: previewLedgerDate('9/10'),\n"
        "  p3: previewLedgerDate('9\\uc6d4 \\uc911'),\n"
        "  p4: previewLedgerDate(''),\n"
        "  top: closeTopModal(),\n"
        "  search: hasActiveSearch(),\n"
        "  discard: confirmDiscardForm('add-ledger-backdrop'),\n"
        "};`;\n"
        "(0, eval)(code);\n"
        "console.log(JSON.stringify(globalThis.__results));\n",
        encoding="utf-8")
    res = subprocess.run(
        ["node", str(driver), str(JS_DIR / "15_ux.js")],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, f"UX 하네스 실패: {res.stderr[:500]}"
    out = json.loads(res.stdout)
    year = datetime.date.today().year
    assert out["p1"] == "→ 2026-09-10 로 저장됩니다", out
    assert out["p2"] == f"→ {year}-09-10 로 저장됩니다", out
    assert out["p3"].startswith("원문 그대로"), out
    assert out["p4"] == "", out
    assert out["top"] is False
    assert out["search"] is False
    assert out["discard"] is True
