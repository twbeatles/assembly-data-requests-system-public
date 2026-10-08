# -*- coding: utf-8 -*-
"""관리대장 '제출' 칸이 빈 행의 진행상태 판정 회귀 테스트.

운영 대장(2019~2025 시트)은 제출한 행마다 '제출'을 직접 적는다. 칸이 비어 있고 제출일도 없는
행은 진행 중인 요구자료인데, 예전 파서는 이를 '제출'로 채워 마감 경고·미제출 필터에서 빠뜨렸다.
(2026-09-15 운영 반영 중 확인: 2026 시트 9건)
"""

import datetime
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db.database_manager import DatabaseManager
from extractors.ledger_parser import load_request_ledger
from services.excel_sync_service import ExcelSyncService
from services.ledger_service import ledger_due_state

HEADERS = ["연번", "소속", "의원", "보좌관", "요구자료", "세부내역", "요구일", "마감일",
           "제출일", "부서", "제출", "비고", "요청형태", "대장ID"]


class BlankStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ledger_status_"))
        self.db_path = self.tmp / "data_requests.db"
        conn = sqlite3.connect(str(self.db_path))
        DatabaseManager.init_schema(conn)
        conn.close()
        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "2026"
        ws.append(HEADERS)
        ws.append([601, "정당", "진행의원", None, "진행 중 자료", "", "9. 14", "9. 16", None, "팀", None, "초안 작성", None, "REQ-2026-001"])
        ws.append([602, "정당", "제출일만", None, "제출일만 적은 자료", "", "9. 10", "9. 12", "9. 12", "팀", None, None, None, "REQ-2026-002"])
        ws.append([603, "정당", "제출의원", None, "제출한 자료", "", "9. 10", "9. 12", "9. 11", "팀", "제출", None, None, "REQ-2026-003"])
        ws.append([604, "정당", "검토의원", None, "검토 중 자료", "", "9. 10", "9. 20", None, "팀", "검토중", None, None, "REQ-2026-004"])
        wb.save(self.tmp / "260904 국회 요구자료 목록(테스트).xlsx")
        wb.close()
        ExcelSyncService._instance = None

    def tearDown(self):
        svc = ExcelSyncService._instance
        if svc is not None:
            svc.stop_file_watcher()
        ExcelSyncService._instance = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parser_status_defaults(self):
        items = load_request_ledger(self.tmp, [], merge_db_records=False, db_path=self.db_path, system_dir=self.tmp)
        status = {it["ledger_id"]: it["status"] for it in items}
        self.assertEqual(status, {
            "REQ-2026-001": "미제출",   # 칸·제출일 모두 비어 있음 → 진행 중
            "REQ-2026-002": "완료",     # 제출일만 있음 → 기존 규칙 유지
            "REQ-2026-003": "제출",     # 직접 적은 값은 그대로
            "REQ-2026-004": "검토중",
        })

    def test_blank_row_counts_as_open_after_sync(self):
        svc = ExcelSyncService(base_dir=self.tmp, db_path=self.db_path, json_path=self.tmp / "data_requests.json")
        ExcelSyncService._instance = svc
        svc.sync_excel_to_db()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = {r["ledger_id"]: dict(r) for r in conn.execute("SELECT * FROM request_ledger")}
        conn.close()
        self.assertEqual(rows["REQ-2026-001"]["status"], "미제출")
        today = datetime.date(2026, 9, 15)
        # 마감 9.16, 기준일 9.15 → 하루 남은 임박 건. 예전에는 '제출'(완료)로 빠졌다.
        self.assertEqual(ledger_due_state(rows["REQ-2026-001"], today), ("soon", 1))
        self.assertEqual(ledger_due_state(rows["REQ-2026-001"], datetime.date(2026, 9, 20))[0], "overdue")
        self.assertEqual(ledger_due_state(rows["REQ-2026-003"], today)[0], "done")

        # 다시 동기화해도 값이 흔들리지 않는다(빈 칸 ↔ '미제출' 사이에서 변경으로 오인하지 않음).
        again = svc.sync_excel_to_db()
        self.assertTrue(again.get("success", True))
        conn = sqlite3.connect(str(self.db_path))
        hist = conn.execute("SELECT COUNT(*) FROM ledger_history WHERE ledger_id='REQ-2026-001'").fetchone()[0]
        status = conn.execute("SELECT status FROM request_ledger WHERE ledger_id='REQ-2026-001'").fetchone()[0]
        conn.close()
        self.assertEqual(status, "미제출")
        self.assertLessEqual(hist, 1)


if __name__ == "__main__":
    unittest.main()
