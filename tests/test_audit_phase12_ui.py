# -*- coding: utf-8 -*-
"""대시보드 UI/UX 개선 회귀 테스트 (Node로 실행).

- CSV 수식 주입 방지(csvCell), 알림 종류 판정(inferNotifyType)
- 중복 실행 방지(runExclusive): 등록 버튼 연타 시 요청 1번, 끝나면 버튼 복구
- 화면 설정 기억(sanitizeViewPrefs): 잘못된 값·정확도순은 기본값으로
- 오프라인 임시 등록 건별 확정(removeLocalLedgerIds): 다른 탭이 추가한 건 보존
- 저장·삭제·전송·설정 저장 진입점이 모두 runExclusive를 거친다
"""

import re
import unittest

from test_audit_phase10_dashboard import NODE, TEMPLATE, run_node


@unittest.skipUnless(NODE, "node가 없어 대시보드 JS 실행 검증을 건너뜁니다")
class DashboardUiHelperTests(unittest.TestCase):
    def test_csv_cell_neutralizes_formulas_and_escapes_quotes(self):
        out = run_node(["csvCell", "csvRow"], """
console.log(JSON.stringify([
  csvCell('=HYPERLINK("http://x","y")'), csvCell('+1'), csvCell('-2+3'), csvCell('@SUM(A1)'),
  csvCell('정상 "인용"'), csvCell(null), csvCell(12), csvCell('abcdef', 3), csvRow(['"a"', '"b"'])
]));
""")
        self.assertEqual(out, [
            '"\'=HYPERLINK(""http://x"",""y"")"', '"\'+1"', '"\'-2+3"', '"\'@SUM(A1)"',
            '"정상 ""인용"""', '""', '"12"', '"abc"', '"a","b"\n',
        ])

    def test_notify_type_inference(self):
        out = run_node(["inferNotifyType"], """
console.log(JSON.stringify([
  inferNotifyType('❌ 등록 실패: x'), inferNotifyType('서버 연결 오류: y'), inferNotifyType('⚠️ 동기화 실패: z'),
  inferNotifyType('✅ 등록되었습니다'), inferNotifyType('표가 복사되었습니다.'),
  inferNotifyType('요구자료명(제목)은 필수 항목입니다.'), inferNotifyType('안내')
]));
""")
        self.assertEqual(out, ["error", "error", "warn", "success", "success", "warn", "info"])

    def test_run_exclusive_blocks_double_submit(self):
        out = run_node(["BUSY_ACTIONS", "runExclusive"], """
(async () => {
  let calls = 0;
  const button = { disabled: false, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; } };
  let release;
  const task = () => { calls += 1; return new Promise(r => { release = r; }); };
  const first = runExclusive('ledger-add', button, task);
  const second = await runExclusive('ledger-add', button, task);
  const lockedWhileRunning = button.disabled && button.attrs['aria-busy'] === 'true';
  release('done');
  const firstResult = await first;
  let failed = false;
  try { await runExclusive('ledger-add', button, async () => { throw new Error('boom'); }); } catch (e) { failed = true; }
  const third = await runExclusive('ledger-add', button, async () => { calls += 1; return 'again'; });
  console.log(JSON.stringify({ calls, second: second === undefined, lockedWhileRunning, firstResult, failed, third,
    restored: button.disabled === false && !('aria-busy' in button.attrs) }));
})();
""")
        self.assertEqual(out, {"calls": 2, "second": True, "lockedWhileRunning": True, "firstResult": "done",
                               "failed": True, "third": "again", "restored": True})

    def test_view_prefs_are_sanitized(self):
        out = run_node(["VIEW_MODES", "VIEW_SORTS", "sanitizeViewPrefs"], """
console.log(JSON.stringify([
  sanitizeViewPrefs({ mode: 'ledger', view: 'table', sort: 'deadline-asc' }),
  sanitizeViewPrefs({ mode: 'evil', view: 'grid', sort: 'relevance' }),
  sanitizeViewPrefs(null), sanitizeViewPrefs('x')
]));
""")
        default = {"mode": "docs", "view": "card", "sort": "date-desc"}
        self.assertEqual(out, [{"mode": "ledger", "view": "table", "sort": "deadline-asc"}, default, default, default])

    def test_offline_sync_commits_per_item_without_clobbering_other_tabs(self):
        out = run_node(["removeLocalLedgerIds"], """
const store = { kocsc_local_ledger: JSON.stringify([{ ledger_id: 'LOCAL-1' }, { ledger_id: 'LOCAL-2' }]) };
localStorage.getItem = k => (k in store ? store[k] : null);
localStorage.setItem = (k, v) => { store[k] = v; };
localStorage.removeItem = k => { delete store[k]; };
const afterFirst = removeLocalLedgerIds(new Set(['LOCAL-1']));
// 전송 도중 다른 탭이 새 임시 등록을 추가했다.
store.kocsc_local_ledger = JSON.stringify(JSON.parse(store.kocsc_local_ledger).concat([{ ledger_id: 'LOCAL-3' }]));
const afterSecond = removeLocalLedgerIds(new Set(['LOCAL-1', 'LOCAL-2']));
const afterAll = removeLocalLedgerIds(new Set(['LOCAL-3']));
console.log(JSON.stringify({ afterFirst, afterSecond, afterAll, removed: !('kocsc_local_ledger' in store) }));
""")
        self.assertEqual(out["afterFirst"], [{"ledger_id": "LOCAL-2"}])
        self.assertEqual(out["afterSecond"], [{"ledger_id": "LOCAL-3"}])
        self.assertEqual(out["afterAll"], [])
        self.assertTrue(out["removed"])


class DashboardUiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_mutating_entry_points_are_exclusive(self):
        for name, key in (("submitNewLedgerItem", "ledger-add"), ("submitEditLedgerItem", "ledger-edit"),
                          ("deleteCurrentLedgerItem", "ledger-delete"), ("syncOfflineLedgerToServer", "offline-sync"),
                          ("handleConfigSubmit", "config-save")):
            with self.subTest(fn=name):
                m = re.search(rf"^function {name}\((?:e)?\) \{{(.*?)^\}}", self.src, re.M | re.S)
                self.assertIsNotNone(m, name)
                assert m is not None
                self.assertIn(f"runExclusive('{key}'", m.group(1))

    def test_feedback_does_not_block_browser(self):
        alerts = re.findall(r"(?<![\w.])alert\(", self.src)
        self.assertEqual(alerts, [])
        self.assertIn("notify(", self.src)

    def test_csv_exports_use_safe_cells(self):
        self.assertNotIn(".replace(/\"/g, '\"\"')}\"`", self.src)
        self.assertGreaterEqual(self.src.count("csvCell("), 30)

    def test_keyboard_and_loading_markup(self):
        self.assertIn('id="app-loading"', self.src)
        self.assertIn('id="toast-stack"', self.src)
        self.assertEqual(self.src.count('class="mode-tab'), self.src.count('role="button" tabindex="0" aria-pressed='))
        self.assertIn("showAppLoadingError(", self.src)
        self.assertIn("restoreViewPrefs();", self.src)

    def test_ledger_edit_modal_stacks_above_detail_modal(self):
        """수정 창은 상세 창보다 앞에 있어야 수정 폼과 저장 버튼을 누를 수 있다."""
        ledger = re.search(r"#ledger-modal-backdrop\s*\{\s*z-index:\s*(\d+)", self.src)
        edit = re.search(r"#edit-ledger-backdrop\s*\{\s*z-index:\s*(\d+)", self.src)
        self.assertIsNotNone(ledger)
        self.assertIsNotNone(edit)
        assert ledger is not None and edit is not None
        self.assertGreater(int(edit.group(1)), int(ledger.group(1)))


if __name__ == "__main__":
    unittest.main()
