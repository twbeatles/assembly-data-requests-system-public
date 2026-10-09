# -*- coding: utf-8 -*-
"""7회차 감사(2026-09-23) 회귀 테스트.

- ISSUE-001: 대시보드 편집 폼이 보내는 13필드 페이로드가 행 전체를 "바뀐 필드"로 만들던 문제
- ISSUE-002: 드롭다운에 없는 상태·요청형태 값이 빈 값으로 저장되던 문제
- ISSUE-003: 엑셀 출처 비ISO 날짜가 웹 수정 전체를 막던 문제
- ISSUE-004: 연도별 배치 전환 후 같은 원문의 마크다운이 두 자리에 남아 문서가 중복되던 문제
- ISSUE-005: `<!-- source: … -->` 역추적 주석이 문서 전문에 섞이던 문제
- 다운로드: 200 헤더 뒤에 파일 열기가 실패하면 오류가 본문에 덧붙던 문제
"""
from typing import Any
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import openpyxl

from test_audit_phase8_fixes import LedgerSyncFixtureMixin, ExcelSyncService, LedgerService
from test_audit_phase10_dashboard import NODE, TEMPLATE, extract_js
from extractors.ledger.dates import coerce_ledger_date, normalize_ledger_date
from pipeline.parse import layout
import file_hash_cache
import parse_all
import extract_and_build_db
import web_server

EDIT_FIELDS = ("seq_no", "party", "requester", "aide", "department", "title", "details",
               "request_date", "deadline", "submit_date", "status", "request_type", "note")


def dashboard_payload(item, **changes):
    """구 대시보드(`submitEditLedgerItemNow`)가 보내던 모양: 모든 폼 필드 + 기대 버전."""
    p = {k: (item.get(k) or "") for k in EDIT_FIELDS}
    p.update(changes)
    p["expected_updated_at"] = item["updated_at"]
    return p


def history_rows(db_path, lid):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT action, changed_fields FROM ledger_history WHERE ledger_id = ? ORDER BY rowid", (lid,))]
    finally:
        conn.close()


class TestFullPayloadEdits(LedgerSyncFixtureMixin, unittest.TestCase):
    """ISSUE-001·002·003: 서버는 실제로 바뀐 필드만 반영한다."""

    def seed(self, row):
        excel = self.make_excel([row])
        self.sync_service().sync_excel_to_db()
        return excel, self.db_rows()[row[0]]

    def test_queued_edit_does_not_revert_manual_excel_edit(self):
        excel, item = self.seed(["REQ-2026-001", "1", "김의원", "자료A", "작성중", "일반", "원래 비고"])
        ledger = self.ledger_service()
        with mock.patch.object(ExcelSyncService, "is_file_locked", return_value=True):
            res = ledger.update_ledger_item("REQ-2026-001", dashboard_payload(item, status="제출"))
        LedgerService.wait_last_sync(3)
        self.assertTrue(res["success"], res)
        self.assertEqual(self.sync_service().protected_field_map()["REQ-2026-001"], {"status"})

        wb = openpyxl.load_workbook(excel)
        wb["2026"].cell(2, 7, "사용자 수기 비고")
        wb.save(excel)
        wb.close()
        self.sync_service().flush_pending_queue()
        row = self.excel_rows(excel)[0]
        self.assertEqual(row["제출"], "제출")
        self.assertEqual(row["비고"], "사용자 수기 비고")

    def test_untouched_masked_cell_is_not_rewritten(self):
        excel, item = self.seed(["REQ-2026-001", "1", "김의원", "자료A", "작성중", "메일", "보좌관 연락처 010-1234-5678"])
        res = self.ledger_service().update_ledger_item("REQ-2026-001", dashboard_payload(item, status="제출"))
        LedgerService.wait_last_sync(3)
        self.assertTrue(res["success"], res)
        self.assertEqual(self.excel_rows(excel)[0]["비고"], "보좌관 연락처 010-1234-5678")
        updates = [h for h in history_rows(self.db_path, "REQ-2026-001") if h["action"] == "UPDATE"]
        self.assertEqual(updates[-1]["changed_fields"], "status,updated_at")
        self.assertEqual(res["changes"], {"status": "제출"})

    def test_identical_payload_is_a_no_op(self):
        excel, item = self.seed(["REQ-2026-001", "1", "김의원", "자료A", "작성중", "메일", ""])
        before_mtime = excel.stat().st_mtime_ns
        before_hist = len(history_rows(self.db_path, "REQ-2026-001"))
        time.sleep(0.05)
        res = self.ledger_service().update_ledger_item("REQ-2026-001", dashboard_payload(item))
        LedgerService.wait_last_sync(3)
        self.assertTrue(res["success"], res)
        self.assertTrue(res.get("unchanged"))
        self.assertEqual(res["updated_at"], item["updated_at"])
        self.assertEqual(excel.stat().st_mtime_ns, before_mtime)
        self.assertEqual(len(history_rows(self.db_path, "REQ-2026-001")), before_hist)

    def test_status_outside_dropdown_survives_other_edit(self):
        excel, item = self.seed(["REQ-2026-001", "1", "김의원", "자료A", "업무설명", "메일", ""])
        # 새 대시보드는 달라진 필드만 보낸다.
        res = self.ledger_service().update_ledger_item(
            "REQ-2026-001", {"note": "메모만 수정", "expected_updated_at": item["updated_at"]})
        LedgerService.wait_last_sync(3)
        self.assertTrue(res["success"], res)
        self.assertEqual(self.excel_rows(excel)[0]["제출"], "업무설명")
        svc = self.sync_service()
        svc._last_known_excel_mtime = 0
        svc.sync_excel_to_db()
        self.assertEqual(self.db_rows()["REQ-2026-001"]["status"], "업무설명")

    def test_blank_status_from_old_client_is_rejected(self):
        excel, item = self.seed(["REQ-2026-001", "1", "김의원", "자료A", "업무설명", "메일", ""])
        res = self.ledger_service().update_ledger_item(
            "REQ-2026-001", dashboard_payload(item, note="메모", status=""))
        self.assertFalse(res["success"])
        self.assertIn("진행상태", res["error"])
        self.assertEqual(self.excel_rows(excel)[0]["제출"], "업무설명")
        self.assertEqual(self.db_rows()["REQ-2026-001"]["status"], "업무설명")

    def test_excel_text_date_does_not_block_unrelated_edit(self):
        self.seed(["REQ-2026-001", "1", "김의원", "자료A", "작성중", "메일", ""])
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("UPDATE request_ledger SET deadline = '9월 중' WHERE ledger_id = 'REQ-2026-001'")
        conn.commit()
        conn.close()
        item = self.db_rows()["REQ-2026-001"]
        ledger = self.ledger_service()
        with mock.patch.object(ExcelSyncService, "is_file_locked", return_value=True):
            ok = ledger.update_ledger_item("REQ-2026-001", dashboard_payload(item, status="제출"))
        self.assertTrue(ok["success"], ok)
        self.assertEqual(self.db_rows()["REQ-2026-001"]["deadline"], "9월 중")
        item = self.db_rows()["REQ-2026-001"]
        with mock.patch.object(ExcelSyncService, "is_file_locked", return_value=True):
            bad = ledger.update_ledger_item("REQ-2026-001", {"deadline": "2026-13-40"})
            norm = ledger.update_ledger_item("REQ-2026-001", {"deadline": "2026. 10. 1."})
        self.assertFalse(bad["success"])
        self.assertTrue(norm["success"], norm)
        self.assertEqual(self.db_rows()["REQ-2026-001"]["deadline"], "2026-10-01")
        LedgerService.wait_last_sync(3)


class TestLedgerDateNormalization(unittest.TestCase):
    def test_korean_official_formats(self):
        for text in ("2026.9.10", "2026. 9. 10.", "2026년 9월 10일", "2026/9/10(수)", "26.9.10", "9/10(수)"):
            self.assertEqual(normalize_ledger_date(text, "2026"), "2026-09-10", text)
            self.assertEqual(coerce_ledger_date(text, "2026"), "2026-09-10", text)

    def test_unreadable_or_impossible(self):
        self.assertEqual(normalize_ledger_date("즉시", "2026"), "즉시")
        self.assertIsNone(coerce_ledger_date("즉시", "2026"))
        self.assertIsNone(coerce_ledger_date("2026-02-30", "2026"))
        self.assertIsNone(coerce_ledger_date("2026.13.1", "2026"))
        self.assertEqual(coerce_ledger_date("", "2026"), "")
        self.assertEqual(coerce_ledger_date(None, "2026"), "")


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 테스트를 건너뜁니다")
class TestEditModalPayload(unittest.TestCase):
    """ISSUE-001·002 클라이언트 쪽: 달라진 필드만 보내고, 목록에 없는 값을 보존한다."""

    def run_js(self, names, body):
        import subprocess
        src = TEMPLATE.read_text(encoding="utf-8")
        prelude = r"""
function makeSelect(values) {
  const sel = { options: [], value: '' };
  values.forEach(v => sel.options.push({ value: v, textContent: v, attrs: {}, getAttribute(k) { return this.attrs[k]; }, setAttribute(k, x) { this.attrs[k] = x; }, remove() { sel.options.splice(sel.options.indexOf(this), 1); } }));
  sel.querySelectorAll = () => sel.options.filter(o => o.attrs['data-preserved'] === '1');
  sel.appendChild = (o) => { o.remove = () => sel.options.splice(sel.options.indexOf(o), 1); sel.options.push(o); };
  Object.defineProperty(sel, 'value', {
    get() { return sel._v === undefined ? '' : sel._v; },
    set(v) { sel._v = sel.options.some(o => o.value === v) ? v : ''; }
  });
  return sel;
}
const document = { createElement: () => ({ attrs: {}, setAttribute(k, x) { this.attrs[k] = x; }, getAttribute(k) { return this.attrs[k]; }, remove() {} }) };
"""
        code = prelude + "\n".join(extract_js(src, n) for n in names) + "\n" + body
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fp:
            fp.write(code)
            path = fp.name
        try:
            assert NODE is not None
            assert NODE is not None
            proc = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8",
                                  timeout=60, stdin=subprocess.DEVNULL)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_select_keeps_value_missing_from_options(self):
        out = self.run_js(["setSelectPreservingValue"], """
const sel = makeSelect(['작성중', '제출', '미제출']);
const res = {};
for (const v of ['업무설명', '완료', '제출']) { setSelectPreservingValue(sel, v); res[v] = sel.value; }
res.preserved = sel.options.filter(o => o.attrs['data-preserved'] === '1').map(o => o.value);
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["업무설명"], "업무설명")
        self.assertEqual(out["완료"], "완료")
        self.assertEqual(out["제출"], "제출")
        self.assertEqual(out["preserved"], [], "임시 선택지는 다음 값을 넣을 때 치운다")

    def test_dirty_fields_only(self):
        out = self.run_js(["ledgerDirtyFields"], """
const before = { title: 'A', status: '업무설명', note: '', deadline: '2026.9.10' };
const after = { title: 'A', status: '업무설명', note: '메모', deadline: '2026.9.10' };
console.log(JSON.stringify([ledgerDirtyFields(before, after), ledgerDirtyFields(after, after)]));
""")
        self.assertEqual(out, [{"note": "메모"}, {}])

    def test_submit_sends_preserved_values_and_dirty_fields_only(self):
        """R-5/#8: `openEditLedgerModal → submitEditLedgerItemNow` 실제 제출 경로.

        상태 `완료`·요청형태 `국정감사`(드롭다운 목록 밖) 항목에서 비고만 고치면,
        폼 값은 보존되고 fetch 본문에는 바뀐 비고와 `expected_updated_at`만 담긴다.
        """
        out = self.run_js(
            ["setSelectPreservingValue", "readEditLedgerForm", "ledgerDirtyFields",
             "openEditLedgerModal", "closeEditLedgerModal", "submitEditLedgerItemNow"],
            """
const EDIT_LEDGER_FIELDS = [
  ['seq_no', 'edit-seq-no'], ['party', 'edit-party'], ['requester', 'edit-requester'],
  ['aide', 'edit-aide'], ['department', 'edit-dept'], ['title', 'edit-title'],
  ['details', 'edit-details'], ['request_date', 'edit-req-date'], ['deadline', 'edit-deadline'],
  ['submit_date', 'edit-submit-date'], ['status', 'edit-status'], ['request_type', 'edit-req-type'],
  ['note', 'edit-note']
];
let EDIT_LEDGER_SNAPSHOT = null;
let currentSelectedLedgerItem = null;
const LEDGER_MAP = {};
const appConfig = { department_name: '부서' };
const ledgerWriteBlockReason = () => null;
const notify = () => {};
const openLedgerModal = () => {};
const render = () => {};
const localStorage = { getItem: () => '[]', setItem: () => {} };
const window = { location: { protocol: 'http:' } };
const __calls = [];
async function fetch(url, opts) {
  __calls.push({ url, body: JSON.parse(opts.body) });
  return { status: 200, ok: true,
    json: async () => ({ success: true, updated_at: '2026-09-25 10:00:00', changes: { note: '새비고' } }) };
}
function makeInput(v) { return { value: v == null ? '' : String(v), classList: { add() {}, remove() {} } }; }
const __els = {
  'edit-id': makeInput(''), 'edit-display-id': makeInput(''),
  'edit-ledger-backdrop': makeInput(''),
  'edit-seq-no': makeInput(''), 'edit-party': makeInput(''), 'edit-requester': makeInput(''),
  'edit-aide': makeInput(''), 'edit-dept': makeInput(''), 'edit-title': makeInput(''),
  'edit-details': makeInput(''), 'edit-req-date': makeInput(''), 'edit-deadline': makeInput(''),
  'edit-submit-date': makeInput(''), 'edit-note': makeInput(''),
  'edit-status': makeSelect(['작성중', '검토중', '제출', '미제출', '해당없음']),
  'edit-req-type': makeSelect(['시스템', '메일', '유선', '서면']),
};
__els['edit-status'].classList = { add() {}, remove() {} };
__els['edit-req-type'].classList = { add() {}, remove() {} };
document.getElementById = (id) => (__els[id] || null);
async function main() {
  currentSelectedLedgerItem = { ledger_id: 'REQ-2026-001', seq_no: '1', party: '',
    requester: '김의원', aide: '', department: '부서', title: '자료A', details: '',
    request_date: '', deadline: '', submit_date: '', status: '완료',
    request_type: '국정감사', note: '원래비고', updated_at: '2026-09-24 00:00:00' };
  LEDGER_MAP['REQ-2026-001'] = currentSelectedLedgerItem;
  openEditLedgerModal();
  const formCheck = { status: __els['edit-status'].value,
    reqType: __els['edit-req-type'].value };
  __els['edit-note'].value = '새비고';
  await submitEditLedgerItemNow();
  console.log(JSON.stringify({ formCheck, calls: __calls,
    mapNote: LEDGER_MAP['REQ-2026-001'].note,
    mapStatus: LEDGER_MAP['REQ-2026-001'].status }));
}
main();
""")
        self.assertEqual(out["formCheck"], {"status": "완료", "reqType": "국정감사"})
        self.assertEqual(len(out["calls"]), 1)
        call = out["calls"][0]
        self.assertTrue(call["url"].endswith("/api/ledger/REQ-2026-001"), call["url"])
        self.assertEqual(call["body"],
                         {"note": "새비고", "expected_updated_at": "2026-09-24 00:00:00"})
        self.assertEqual(out["mapNote"], "새비고")
        self.assertEqual(out["mapStatus"], "완료")

    def test_edit_status_dropdown_lists_done_statuses(self):
        shell = (SCRIPTS_DIR / "templates" / "dashboard" / "shell.html").read_text(encoding="utf-8")
        block = shell[shell.index('id="edit-status"'):]
        block = block[:block.index("</select>")]
        for value in ("완료", "업무설명", "제출", "미제출"):
            self.assertIn(f'value="{value}"', block)


class LayoutFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="audit_r7_layout_"))
        self.root = self.tmp / "ws"
        self.sysd = self.root / "system"
        self.out = self.sysd / "_parsed_markdown"
        self.out.mkdir(parents=True)
        self.cache = file_hash_cache.FileHashCache(self.tmp / "hc.json")
        self._patches = [
            mock.patch.object(file_hash_cache, "_default", self.cache),
            mock.patch.object(parse_all, "ROOT_DIR", self.root),
            mock.patch.object(parse_all, "SYSTEM_DIR", self.sysd),
            mock.patch.object(parse_all, "OUTPUT_DIR", self.out),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def source(self, rel, data=b"hwp"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def md(self, rel, body, src_hash=None, header=None):
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (f"<!-- source: {header} -->\n" if header else "") + body
        path.write_text(text, encoding="utf-8")
        if src_hash:
            path.with_name(path.name + ".sha256").write_text(src_hash, encoding="utf-8")
        return path


class TestMarkdownLayout(LayoutFixture):
    """ISSUE-004: 같은 원문의 결과는 정해진 자리 한 곳에만 있다."""

    def test_legacy_copy_is_moved_with_hash_and_counts_as_cached(self):
        src = self.source("홍길동 519/260901 요구자료.hwp")
        legacy = self.md("홍길동 519/260901 요구자료.hwp.md", "# 본문\n", self.cache.sha256(src))
        self.assertFalse(parse_all.is_parse_cache_fresh(src, self.out))
        res = parse_all.consolidate_markdown_layout([src])
        expected, expected_hash = parse_all.parse_output_paths(src, self.out)
        self.assertEqual(len(res["moved"]), 1)
        self.assertFalse(legacy.exists())
        self.assertTrue(expected.exists() and expected_hash.exists())
        self.assertTrue(parse_all.is_parse_cache_fresh(src, self.out), "KorDoc 없이도 캐시로 인정돼야 한다")

    def test_duplicate_is_retired_to_backup_not_deleted(self):
        src = self.source("260831 입법조사관 요청자료.hwp")
        h = self.cache.sha256(src)
        legacy = self.md("260831 입법조사관 요청자료.hwp.md", "옛 본문", h)
        current = self.md("2026/260831 입법조사관 요청자료.hwp.md", "새 본문", h,
                          header="260831 입법조사관 요청자료.hwp")
        res = parse_all.consolidate_markdown_layout([src])
        self.assertEqual(res["moved"], [])
        self.assertEqual(res["removed"], ["260831 입법조사관 요청자료.hwp.md"])
        self.assertFalse(legacy.exists())
        self.assertTrue(current.exists())
        backups = list((self.sysd / ".maintenance_backup").rglob("*.hwp.md"))
        self.assertEqual(len(backups), 1)

    def test_flattened_copy_from_old_reorganize_tool_is_consolidated(self):
        """이전 재배치 도구가 '미분류' 하위 경로를 평탄화한 사본도 주석으로 원문을 찾아 모은다."""
        src = self.source("미분류/하위/260902 자료.hwp")
        stray = self.md("2026/260902 자료.hwp.md", "본문", self.cache.sha256(src), header="미분류/하위/260902 자료.hwp")
        parse_all.consolidate_markdown_layout([src])
        expected, _ = parse_all.parse_output_paths(src, self.out)
        self.assertEqual(expected, self.out / "미분류" / "하위" / "260902 자료.hwp.md")
        self.assertTrue(expected.exists())
        self.assertFalse(stray.exists())

    def test_unrelated_markdown_is_left_alone(self):
        self.source("2026/a.hwp")
        orphan = self.md("2025/없는원문.hwp.md", "본문")
        res = parse_all.consolidate_markdown_layout([self.root / "2026" / "a.hwp"])
        self.assertEqual(res, {"moved": [], "removed": []})
        self.assertTrue(orphan.exists())

    def test_source_header_write_is_atomic(self):
        md = self.md("2026/x.hwp.md", "# 본문\n")
        with mock.patch.object(layout.os, "replace", side_effect=OSError("disk")):
            parse_all._ensure_source_header(md, Path("2026/x.hwp"))
        self.assertEqual(md.read_text(encoding="utf-8"), "# 본문\n", "실패해도 원본 본문이 그대로여야 한다")
        self.assertEqual([p.name for p in md.parent.iterdir()], ["x.hwp.md"], "임시 파일을 남기지 않는다")


class TestBuilderDedupAndHeader(LayoutFixture):
    """ISSUE-004(최후 방어선)·ISSUE-005: DB 빌더."""

    def build(self):
        patches = [
            mock.patch.object(extract_and_build_db, "ROOT_DIR", self.root),
            mock.patch.object(extract_and_build_db, "SYSTEM_DIR", self.sysd),
            mock.patch.object(extract_and_build_db, "PARSED_DIR", self.out),
            mock.patch.object(extract_and_build_db, "DB_PATH", self.sysd / "data_requests.db"),
            mock.patch.object(extract_and_build_db, "JSON_PATH", self.sysd / "data_requests.json"),
            mock.patch.object(extract_and_build_db, "DIST_MANIFEST", self.sysd / ".distribution_manifest.json"),
            mock.patch.object(extract_and_build_db, "prepare_ledger_for_rebuild", return_value=set()),
            mock.patch.object(extract_and_build_db, "load_request_ledger", return_value=[]),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            res = extract_and_build_db.main()
        conn = sqlite3.connect(str(self.sysd / "data_requests.db"))
        try:
            rows = conn.execute("SELECT doc_id, original_path, parsed_md_path, full_markdown FROM documents").fetchall()
            qas = conn.execute("SELECT qa_id, doc_id, answer_markdown FROM qa_items").fetchall()
        finally:
            conn.close()
        return res, rows, qas

    def test_two_copies_of_one_source_load_as_one_document(self):
        src = self.source("홍길동 519/260901 홍길동의원 요구자료.hwp")
        self.source("2026/260902 이의원 요구자료.hwp")
        body = "# 요구자료\n\n1. 질의 내용입니다?\n\n○ 답변 내용이 여기에 있습니다 충분히 긴 문장.\n"
        self.md("홍길동 519/260901 홍길동의원 요구자료.hwp.md", body)
        newer = self.md("2026/홍길동 519/260901 홍길동의원 요구자료.hwp.md", body,
                        header="홍길동 519/260901 홍길동의원 요구자료.hwp")
        self.md("2026/260902 이의원 요구자료.hwp.md", body)
        res, rows, qas = self.build()
        self.assertTrue(res["success"], res)
        self.assertEqual(res["documents"], 2)
        originals = sorted(r[1] for r in rows)
        self.assertEqual(originals, ["2026/260902 이의원 요구자료.hwp", "홍길동 519/260901 홍길동의원 요구자료.hwp"])
        kept = [r for r in rows if r[1].startswith("홍길동")][0]
        self.assertEqual(kept[2], "_parsed_markdown/" + newer.relative_to(self.out).as_posix())
        doc_ids = {r[0] for r in rows}
        self.assertTrue(all(q[1] in doc_ids for q in qas), "버린 사본의 Q&A가 남으면 안 된다")

    def test_source_header_is_not_part_of_full_text(self):
        self.source("2026/a.hwp")
        self.md("2026/a.hwp.md", "# 제목\n본문 내용\n", header="2026/a.hwp")
        _res, rows, qas = self.build()
        self.assertEqual(rows[0][3], "# 제목\n본문 내용\n")
        self.assertTrue(all("source:" not in (q[2] or "") for q in qas))


class TestDownloadOpenFailure(unittest.TestCase):
    def test_open_failure_is_reported_before_headers(self):
        base = Path(tempfile.mkdtemp(prefix="audit_r7_dl_"))
        try:
            doc_dir = base / "2026"
            doc_dir.mkdir(parents=True)
            (doc_dir / "잠긴_파일.md").write_text("x", encoding="utf-8")
            handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
            sent = []
            handler.send_response = lambda code: sent.append(("status", code))
            handler.send_header = lambda k, v: sent.append((k, v))
            handler.end_headers = lambda: sent.append(("end", None))
            handler.send_error_response = lambda code, msg: sent.append(("error", code))
            handler.wfile = io.BytesIO()
            handler.headers = {}
            from urllib.parse import quote
            handler.path = "/api/download?path=" + quote("2026/잠긴_파일.md")
            with mock.patch.object(web_server, "BASE_DIR", base), \
                 mock.patch("web.routes_get.open", side_effect=PermissionError("locked"), create=True):
                handler.do_GET()
            self.assertEqual(sent, [("error", 500)])
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
