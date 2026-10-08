import os
import sys
import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract_and_build_db
import web_server
import parse_all
import package_distribution

class TestAuditImprovements(unittest.TestCase):
    def test_1_ledger_rendering_and_modals_in_html_script(self):
        """Audit Item 1: Verify dashboard script has dedicated ledger rendering, modals, and sync."""
        tpl_file = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        code = tpl_file.read_text(encoding='utf-8') if tpl_file.exists() else (SCRIPTS_DIR / "generate_web_dashboard.py").read_text(encoding='utf-8')

        # Dedicated ledger mode branch in render()
        self.assertIn("else if (currentMode === 'ledger')", code)
        self.assertIn("대장 ID</th>", code)
        self.assertIn("openLedgerModal(item)", code)

        # count-total fixed for ledger mode
        self.assertIn("if (currentMode === 'ledger') totalCount = REQUEST_LEDGER.length;", code)

        # Edit and delete ledger modals and functions
        self.assertIn('id="edit-ledger-backdrop"', code)
        self.assertIn('id="btn-edit-ledger"', code)
        self.assertIn('id="btn-delete-ledger"', code)
        self.assertIn('openEditLedgerModal', code)
        self.assertIn('submitEditLedgerItem', code)
        self.assertIn('deleteCurrentLedgerItem', code)

        # Offline sync banner and functions
        self.assertIn('id="offline-sync-banner"', code)
        self.assertIn('syncOfflineLedgerToServer', code)
        self.assertIn('fetchLatestLedgerFromServer', code)

    def test_2_extract_and_build_db_preserves_user_ledger(self):
        """Audit Item 2: Verify load_request_ledger preserves user-created and updated DB records."""
        temp_dir = tempfile.mkdtemp()
        try:
            temp_db = Path(temp_dir) / "data_requests.db"
            conn = sqlite3.connect(str(temp_db))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE request_ledger (
                    ledger_id TEXT PRIMARY KEY, year TEXT, seq_no TEXT, party TEXT, requester TEXT,
                    aide TEXT, title TEXT, details TEXT, request_date TEXT, deadline TEXT,
                    submit_date TEXT, department TEXT, status TEXT, note TEXT, request_type TEXT,
                    linked_doc_id TEXT, created_at TEXT, updated_at TEXT
                )
            """)
            cur.execute("""
                INSERT INTO request_ledger VALUES (
                    'REQ-2026-999', '2026', 'CUSTOM-999', '혁신당', '홍길동 의원',
                    '김보좌', '사용자 등록 테스트 요구자료', '상세내역', '2026-09-08', '2026-09-10',
                    '2026-09-09', '전담팀', '완료', '비고 메모', '시스템', '',
                    '2026-09-08 10:00:00', '2026-09-08 10:00:00'
                )
            """)
            conn.commit()
            conn.close()

            # 운영 워크스페이스(ROOT_DIR)가 아니라 임시 폴더만 본다. 예전에는 운영 대장 엑셀을
            # 읽고 모듈 전역 DB_PATH 패치에 기대 병합 DB를 바꿨다. (감사 R4-01/R4-02)
            records = extract_and_build_db.load_request_ledger(
                Path(temp_dir), [], db_path=temp_db, system_dir=Path(temp_dir))
            ids = [r["ledger_id"] for r in records]
            self.assertIn("REQ-2026-999", ids)
            custom_item = next(r for r in records if r["ledger_id"] == "REQ-2026-999")
            self.assertEqual(custom_item["requester"], "홍길동 의원")
            self.assertEqual(custom_item["title"], "사용자 등록 테스트 요구자료")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_3_year_regex_supports_2018_to_2039(self):
        """Audit Item 8: Verify date & year regex supports 2018 through 2039."""
        meta_2018 = extract_and_build_db.extract_metadata("2018/180528_의원실_요구자료.hwp")
        self.assertEqual(meta_2018["year"], "2018")
        self.assertEqual(meta_2018["request_date"], "2018-05-28")

        meta_2021 = extract_and_build_db.extract_metadata("2021/210715_과방위_요구자료.hwp")
        self.assertEqual(meta_2021["year"], "2021")
        self.assertEqual(meta_2021["request_date"], "2021-07-15")

        meta_2026 = extract_and_build_db.extract_metadata("2026/260908_김남국_의원_요구자료.hwp")
        self.assertEqual(meta_2026["year"], "2026")
        self.assertEqual(meta_2026["request_date"], "2026-09-08")

        meta_2030 = extract_and_build_db.extract_metadata("2030/300101_미래_요구자료.hwp")
        self.assertEqual(meta_2030["year"], "2030")
        self.assertEqual(meta_2030["request_date"], "2030-01-01")

    def test_4_web_server_binds_to_localhost_and_restricts_cors(self):
        """Audit Item 4: Verify web_server binds to 127.0.0.1 and restricts CORS."""
        server_code = (SCRIPTS_DIR / "web_server.py").read_text(encoding='utf-8')
        security_code = (SCRIPTS_DIR / "web" / "security.py").read_text(encoding='utf-8')
        self.assertIn("server_address = ('127.0.0.1', p)", server_code)
        self.assertNotIn("server_address = ('', port)", server_code)
        self.assertIn("origin.startswith('http://127.0.0.1:')", security_code)

    def test_5_web_server_concurrency_protection(self):
        """Audit Item 5: Verify POST /api/sync concurrency protection."""
        server_code = (SCRIPTS_DIR / "web_server.py").read_text(encoding='utf-8')
        self.assertIn("SYNC_LOCK", server_code)
        self.assertIn("IS_SYNCING", server_code)
        self.assertIn("409", server_code)
        self.assertIn("JSON_LOCK", server_code)

    def test_6_parse_all_uses_safe_subprocess(self):
        """Audit Item 6: Verify parse_all uses shell=False."""
        parse_code = (SCRIPTS_DIR / "parse_all.py").read_text(encoding='utf-8')
        self.assertIn("shell=False", parse_code)
        self.assertNotIn("shell=True", parse_code)

    def test_7_package_distribution_and_docs_alignment(self):
        """Audit Item 7: Verify package distribution includes all 4 batch files and 5 sheets."""
        dist_code = (SCRIPTS_DIR / "package_distribution.py").read_text(encoding='utf-8')
        self.assertIn("00_새자료_추가_및_DB동기화.bat", dist_code)
        self.assertIn("01_웹대시보드_실행.bat", dist_code)
        self.assertIn("02_웹관리서버_실행.bat", dist_code)
        self.assertIn("03_엑셀DB_열기.bat", dist_code)
        self.assertIn("5개 시트", dist_code)

        smart_add_code = (SCRIPTS_DIR / "add_documents_smart.py").read_text(encoding='utf-8')
        self.assertIn("5개 시트", smart_add_code)

    def test_8_dashboard_ledger_dom_and_favorites(self):
        """Audit Verification: Ensure dashboard JS properly updates ledger tab active class, ledger count, and favorite filtering."""
        tpl_file = SCRIPTS_DIR / "templates" / "dashboard_template.html"
        code = tpl_file.read_text(encoding='utf-8') if tpl_file.exists() else (SCRIPTS_DIR / "generate_web_dashboard.py").read_text(encoding='utf-8')

        # 1. switchMode must toggle active state for tab-mode-ledger (탭 활성화는 syncModeTabs가 맡는다)
        self.assertIn("tab-mode-ledger", code)
        self.assertIn("syncModeTabs();", code)
        from test_audit_phase10_dashboard import NODE, run_node
        if NODE:
            out = run_node(["VIEW_MODES", "syncModeTabs"], """
const tabs = {};
['docs', 'qa', 'tables', 'ledger'].forEach(m => {
  tabs[m] = { active: m === 'docs', attrs: {},
    classList: { toggle(name, on) { tabs[m].active = on; } },
    setAttribute(k, v) { this.attrs[k] = v; } };
});
document.getElementById = id => tabs[id.replace('tab-mode-', '')] || null;
let currentMode = 'ledger';
syncModeTabs();
console.log(JSON.stringify(Object.fromEntries(Object.entries(tabs).map(([k, t]) => [k, [t.active, t.attrs['aria-pressed']]]))));
""")
            self.assertEqual(out, {"docs": [False, "false"], "qa": [False, "false"],
                                   "tables": [False, "false"], "ledger": [True, "true"]})

        # 2. init must populate count-mode-ledger
        self.assertIn("count-mode-ledger", code)
        self.assertIn("cntLedger.innerText = REQUEST_LEDGER.length", code)

        # 3. filterItems favorites filter must support item.ledger_id
        self.assertIn("item.doc_id || item.qa_id || item.ledger_id", code)

        # 4. loadAndDecompressDatabase should accept valid server data
        self.assertIn("REQUEST_LEDGER = sdata.data", code)

if __name__ == "__main__":
    unittest.main()


