from typing import Any
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
from db.database_manager import DatabaseManager

class TestLedgerAndServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.test_db_path = Path(cls.temp_dir) / "test_data_requests.db"
        cls.test_json_path = Path(cls.temp_dir) / "test_data_requests.json"
        
        # Always use a deterministic fixture: production data is deliberately
        # gitignored and must not influence source-test behavior.
        conn = sqlite3.connect(str(cls.test_db_path))
        DatabaseManager.init_schema(conn)
        conn.execute("INSERT INTO request_ledger (ledger_id, year, requester, title, request_type, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("REQ-2026-001", "2026", "테스트의원", "딥페이크 테스트", "일반", "작성중", "2026-01-01", "2026-01-01"))
        conn.commit()
        conn.close()
        
        # Point web_server to test DB and JSON
        cls.orig_db_path = web_server.DB_PATH
        cls.orig_json_path = web_server.JSON_PATH
        cls.orig_base_dir = web_server.BASE_DIR
        web_server.set_db_paths(cls.test_db_path, cls.test_json_path)
        web_server.BASE_DIR = Path(cls.temp_dir)

    @classmethod
    def tearDownClass(cls):
        web_server.wait_last_sync(timeout=3.0)
        web_server.set_db_paths(cls.orig_db_path, cls.orig_json_path)
        web_server.BASE_DIR = cls.orig_base_dir
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_normalize_ledger_date(self):
        self.assertEqual(extract_and_build_db.normalize_ledger_date("2026-01-02 00:00:00", "2026"), "2026-01-02")
        self.assertEqual(extract_and_build_db.normalize_ledger_date("9. 7", "2026"), "2026-09-07")
        self.assertEqual(extract_and_build_db.normalize_ledger_date("9.4", "2026"), "2026-09-04")
        self.assertEqual(extract_and_build_db.normalize_ledger_date("0528", "2018"), "2018-05-28")
        self.assertEqual(extract_and_build_db.normalize_ledger_date("2020-03-24", "2020"), "2020-03-24")
        self.assertEqual(extract_and_build_db.normalize_ledger_date("", "2026"), "")

    def test_ledger_in_database(self):
        self.assertTrue(self.test_db_path.exists())
        conn = sqlite3.connect(str(self.test_db_path))
        cur = conn.cursor()
        
        # Fixture or deployment database must expose ledger rows and FTS.
        count = cur.execute("SELECT COUNT(*) FROM request_ledger").fetchone()[0]
        self.assertGreaterEqual(count, 1)

        # Check FTS5 search on ledger
        cur.execute("SELECT rowid, title, requester FROM request_ledger_fts WHERE request_ledger_fts MATCH '딥페이크'")
        fts_rows = cur.fetchall()
        self.assertGreater(len(fts_rows), 0)

        # Check linked docs
        linked_count = cur.execute("SELECT COUNT(*) FROM request_ledger WHERE linked_doc_id != ''").fetchone()[0]
        self.assertGreaterEqual(linked_count, 0)

        conn.close()

    def test_web_server_crud_and_validation(self):
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        
        # 1. Validation error check: empty title
        invalid_res = handler.insert_ledger_item({"year": "2026", "requester": "의원"})
        self.assertFalse(invalid_res.get("success"))
        self.assertIn("필수 입력", invalid_res.get("error", ""))

        # 2. Validation error check: empty requester
        invalid_res2 = handler.insert_ledger_item({"year": "2026", "title": "제목만 있음"})
        self.assertFalse(invalid_res2.get("success"))
        self.assertIn("필수 입력", invalid_res2.get("error", ""))

        # 3. Valid insert
        test_payload = {
            "year": "2026",
            "seq_no": "TEST-01",
            "party": "테스트당",
            "requester": "단위테스트의원",
            "aide": "테스트보좌관",
            "title": "단위테스트 요구자료 제목",
            "details": "단위테스트 요구자료 세부내역 1. 2. 3.",
            "department": "기획예산팀",
            "status": "작성중"
        }
        res = handler.insert_ledger_item(test_payload)
        self.assertTrue(res.get("success"))
        item = res.get("item")
        new_id = item.get("ledger_id")

        # 4. Query check
        found = handler.get_ledger_item(new_id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found["requester"], "단위테스트의원")
        self.assertEqual(found["title"], "단위테스트 요구자료 제목")

        # 5. Validation error on update: empty title
        up_invalid = handler.update_ledger_item(new_id, {"title": ""})
        self.assertFalse(up_invalid.get("success"))

        # 6. Valid Update check
        up_res = handler.update_ledger_item(new_id, {"status": "제출"})
        self.assertTrue(up_res.get("success"))
        found_up = handler.get_ledger_item(new_id)
        assert found_up is not None
        self.assertEqual(found_up["status"], "제출")

        # 7. Delete check
        del_res = handler.delete_ledger_item(new_id)
        self.assertTrue(del_res.get("success"))
        self.assertIsNone(handler.get_ledger_item(new_id))

    def test_trailing_slash_and_unquote_parsing(self):
        """Test URL parsing for ledger items with trailing slash and percent-encoded characters."""
        from urllib.parse import unquote
        
        test_paths = [
            ("/api/ledger/REQ-2026-001/", "REQ-2026-001"),
            ("/api/ledger/REQ-2026-001///", "REQ-2026-001"),
            ("/api/ledger/REQ%2D2026%2D001", "REQ-2026-001"),
            ("/api/ledger/%ED%85%8C%EC%8A%A4%ED%8A%B8", "테스트"),
        ]
        
        for p, expected_id in test_paths:
            extracted_id = unquote(p.rstrip('/').split('/')[-1])
            self.assertEqual(extracted_id, expected_id)
            
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        # Fetch an existing item with parsed ID
        conn = sqlite3.connect(str(self.test_db_path))
        first_row = conn.cursor().execute("SELECT ledger_id FROM request_ledger LIMIT 1").fetchone()
        conn.close()
        if first_row:
            target_id = first_row[0]
            item = handler.get_ledger_item(target_id)
            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item["ledger_id"], target_id)

    def test_short_query_fallback_qa_search(self):
        """Test fallback LIKE search for Q&A items with keywords shorter than 3 characters."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        
        # Test 1 or 2 character keywords that trigger fallback LIKE search
        # Previously failed with 'no such column: q.question_title' when using older init_db schema
        try:
            results_1char = handler.query_qa_items(kw="법")
            self.assertIsInstance(results_1char, list)
            results_2char = handler.query_qa_items(kw="통신")
            self.assertIsInstance(results_2char, list)
        except Exception as e:
            self.fail(f"Short keyword fallback search raised an unexpected error: {e}")

    def test_search_category_and_type_compatibility(self):
        """Test search_all supporting various category / type filters."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)
        
        for stype in ["all", "docs", "qa", "ledger"]:
            res = handler.search_all(kw="자료", search_type=stype)
            self.assertTrue(res.get("success"))
            self.assertIn("counts", res)
            if stype == "docs":
                self.assertEqual(len(res["qa_items"]), 0)
                self.assertEqual(len(res["ledger"]), 0)
            elif stype == "qa":
                self.assertEqual(len(res["documents"]), 0)
                self.assertEqual(len(res["ledger"]), 0)
            elif stype == "ledger":
                self.assertEqual(len(res["documents"]), 0)
                self.assertEqual(len(res["qa_items"]), 0)

    def test_file_download_endpoint_and_security(self):
        """Test path traversal protection for file download endpoint."""
        ws_root = SYSTEM_DIR.parent if (SYSTEM_DIR.parent / "2026").exists() else SYSTEM_DIR
        
        # 1. Path traversal attempts
        traversal_attempts = [
            "../../windows/system32/cmd.exe",
            "..\\..\\..\\secret.txt",
            "/etc/passwd",
            "C:\\Windows\\win.ini"
        ]
        
        for attempt in traversal_attempts:
            norm = os.path.normpath(attempt)
            if norm.startswith(('\\', '/')) or (len(norm) > 1 and norm[1] == ':'):
                full_path = Path(norm).resolve()
            else:
                candidate = (ws_root / norm).resolve()
                if not candidate.exists() and (SYSTEM_DIR / norm).exists():
                    full_path = (SYSTEM_DIR / norm).resolve()
                else:
                    full_path = candidate

            is_safe = False
            try:
                if full_path.is_relative_to(SYSTEM_DIR) or full_path.is_relative_to(ws_root):
                    is_safe = True
            except (ValueError, AttributeError):
                is_safe = False
            
            # Traversal outside workspace root must not be considered safe
            self.assertFalse(is_safe, f"Path traversal should be blocked: {attempt}")

        # 2. Legitimate workspace file
        normal_file = "README.md"
        norm = os.path.normpath(normal_file)
        candidate = (ws_root / norm).resolve()
        if not candidate.exists() and (SYSTEM_DIR / norm).exists():
            full_path = (SYSTEM_DIR / norm).resolve()
        else:
            full_path = candidate

        self.assertTrue(full_path.is_relative_to(SYSTEM_DIR) or full_path.is_relative_to(ws_root))
        self.assertTrue(full_path.exists())

if __name__ == "__main__":
    unittest.main()

