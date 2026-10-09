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
from db import fts_spec
import web_server
import generate_web_dashboard

class TestDbAndSearchAdvanced(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.test_db_path = Path(cls.temp_dir) / "test_advanced.db"
        cls.test_json_path = Path(cls.temp_dir) / "test_advanced.json"

        cls.orig_db_path = web_server.DB_PATH
        cls.orig_json_path = web_server.JSON_PATH
        cls.orig_base_dir = web_server.BASE_DIR
        web_server.set_db_paths(cls.test_db_path, cls.test_json_path)
        # BASE_DIR를 바꾸지 않으면 등록 테스트가 운영 마스터 엑셀·보류 큐에 쓴다. (감사 R3-01)
        web_server.BASE_DIR = Path(cls.temp_dir)

        conn = sqlite3.connect(str(cls.test_db_path))
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL;")
        cur.execute("PRAGMA foreign_keys = ON;")

        cur.execute("""
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                year TEXT,
                request_date TEXT,
                institution TEXT,
                requester TEXT,
                doc_number TEXT,
                version TEXT,
                title TEXT,
                department TEXT,
                contact_person TEXT,
                question_list TEXT,
                answer_summary TEXT,
                answer_full_text TEXT,
                has_tables TEXT,
                table_count INTEGER,
                table_summary TEXT,
                topic_tags TEXT,
                parsed_md_path TEXT,
                original_path TEXT,
                full_markdown TEXT,
                linked_ledger_id TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE qa_items (
                qa_id TEXT PRIMARY KEY,
                doc_id TEXT,
                year TEXT,
                request_date TEXT,
                institution TEXT,
                requester TEXT,
                doc_number TEXT,
                q_num INTEGER,
                question_title TEXT,
                answer_full TEXT,
                answer_markdown TEXT,
                has_tables TEXT,
                parsed_md_path TEXT,
                original_path TEXT,
                FOREIGN KEY (doc_id) REFERENCES documents (doc_id)
            )
        """)

        cur.execute("""
            CREATE TABLE request_ledger (
                ledger_id TEXT PRIMARY KEY,
                year TEXT,
                seq_no TEXT,
                party TEXT,
                requester TEXT,
                aide TEXT,
                title TEXT,
                details TEXT,
                request_date TEXT,
                deadline TEXT,
                submit_date TEXT,
                department TEXT,
                status TEXT,
                note TEXT,
                request_type TEXT,
                linked_doc_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # FTS5 Trigram Virtual Tables — 정의는 운영 코드와 같은 fts_spec에서 가져온다.
        # 손으로 적으면 운영 인덱스가 바뀔 때 픽스처만 옛 컬럼에 남아 조용히 갈라진다.
        for _fts_name in fts_spec.fts_table_names():
            cur.execute(fts_spec.create_sql(_fts_name))

        # Insert test sample document
        cur.execute("""
            INSERT INTO documents VALUES (
                'DOC-2026-001', '2026', '2026-09-01', '국회', '홍길동 의원', '0517',
                'v1', '기획예산팀 딥페이크 대응 현황 요구', '기획예산팀', '김담당',
                '1. 딥페이크 성범죄 영상물 접속차단 현황', '2026년 8월까지 15,000건 시정요구 완료',
                '본 전담팀에서는 텔레그램 핫라인 및 신속심의 체계를 구축하여 대응 중임.',
                'Y', 2, '연도별 시정요구 현황표 수록', '딥페이크,텔레그램,시정요구',
                '_parsed_markdown/2026/test.md', '2026/260901 홍길동 의원 요구자료(0517).hwp',
                '# 전문 마크다운\n\n본 전담팀에서는 텔레그램 핫라인 및 신속심의 체계를 구축하여 대응 중임.', ''
            )
        """)

        # full_markdown이 정본이고 answer_full_text는 그 엑셀 잘림본이다. 픽스처도 같은 관계를
        # 지켜야 한다. 예전 픽스처는 본문을 잘림본에만 넣어, 정본을 색인하면 검색이 0건이 됐다.
        cur.execute(fts_spec.populate_sql("documents_fts"))

        # Insert test Q&A item
        cur.execute("""
            INSERT INTO qa_items VALUES (
                'DOC-2026-001-Q1', 'DOC-2026-001', '2026', '2026-09-01', '국회', '홍길동 의원', '0517',
                1, '딥페이크 허위영상물 신속심의 및 접속차단 실적',
                '텔레그램을 통한 불법촬영물 유포에 대해 24시간 상시 모니터링을 진행하고 즉각 시정요구함.',
                '답변 마크다운 내용', 'Y', '_parsed_markdown/2026/test.md', '2026/test.hwp'
            )
        """)

        cur.execute(fts_spec.populate_sql("qa_items_fts"))

        # Insert test Ledger item
        cur.execute("""
            INSERT INTO request_ledger VALUES (
                'REQ-2026-001', '2026', '0517', '국민의힘', '홍길동 의원', '이보좌',
                '정보통신망 관련 플랫폼별 시정요구 및 심의 내역',
                '최근 3개년 텔레그램, 트위터, 인스타그램 조치 현황',
                '2026-09-01', '2026-09-03', '2026-09-02', '기획예산팀',
                '제출', '완료', '공문서', 'DOC-2026-001',
                '2026-09-01 09:00:00', '2026-09-01 09:00:00'
            )
        """)

        cur.execute(fts_spec.populate_sql("request_ledger_fts"))

        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        web_server.wait_last_sync(timeout=3.0)
        web_server.set_db_paths(cls.orig_db_path, cls.orig_json_path)
        web_server.BASE_DIR = cls.orig_base_dir
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_fts5_trigram_substring_search(self):
        """Test that FTS5 trigram accurately finds Korean substrings inside compound words without spaces."""
        conn = web_server.get_db_conn()
        cur = conn.cursor()

        # '전담팀' is a substring inside '기획예산팀'
        res = cur.execute("""
            SELECT d.doc_id, d.title FROM documents d
            JOIN documents_fts ON d.rowid = documents_fts.rowid
            WHERE documents_fts MATCH '전담팀'
        """).fetchall()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["doc_id"], "DOC-2026-001")

        # '핫라인' inside '텔레그램 핫라인'
        res_hl = cur.execute("""
            SELECT d.doc_id FROM documents d
            JOIN documents_fts ON d.rowid = documents_fts.rowid
            WHERE documents_fts MATCH '핫라인'
        """).fetchall()
        self.assertEqual(len(res_hl), 1)

        # '허위영상물' inside Q&A title
        res_qa = cur.execute("""
            SELECT q.qa_id FROM qa_items q
            JOIN qa_items_fts ON q.rowid = qa_items_fts.rowid
            WHERE qa_items_fts MATCH '허위영상물'
        """).fetchall()
        self.assertEqual(len(res_qa), 1)

        conn.close()

    def test_web_server_pragmas(self):
        """Test that get_db_conn applies busy_timeout, WAL journal mode, and foreign keys."""
        conn = web_server.get_db_conn()
        cur = conn.cursor()

        busy_timeout = cur.execute("PRAGMA busy_timeout;").fetchone()[0]
        self.assertEqual(busy_timeout, 5000)

        fk = cur.execute("PRAGMA foreign_keys;").fetchone()[0]
        self.assertEqual(fk, 1)

        conn.close()

    def test_web_server_query_ledger_fts_and_fallback(self):
        """Test query_ledger uses FTS5 trigram + BM25 and falls back to LIKE for short terms."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)

        # FTS5 Trigram query (>= 3 chars)
        res1 = handler.query_ledger(kw="플랫폼별")
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["ledger_id"], "REQ-2026-001")

        # 2-char keyword falls back to LIKE seamlessly
        res2 = handler.query_ledger(kw="심의")
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["ledger_id"], "REQ-2026-001")

    def test_web_server_search_all_integrated_endpoint(self):
        """Test search_all returns unified counts and results across ledger, documents, and QA."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)

        res = handler.search_all(kw="딥페이크")
        self.assertTrue(res["success"])
        self.assertGreater(res["counts"]["total"], 0)
        self.assertIn("documents", res)
        self.assertIn("qa_items", res)
        self.assertIn("ledger", res)

    def test_auto_link_ledger_to_doc_on_insert(self):
        """Test that insert_ledger_item automatically finds matching doc by seq_no and creates bilateral links."""
        handler: Any = web_server.RequestLedgerHandler.__new__(web_server.RequestLedgerHandler)

        new_item_payload = {
            "year": "2026",
            "seq_no": "0517",  # matches DOC-2026-001
            "requester": "홍길동 의원",
            "title": "추가 자동 연계 테스트 자료",
            "details": "세부 내용"
        }
        res = handler.insert_ledger_item(new_item_payload)
        self.assertTrue(res["success"])
        created = res["item"]

        # Check linked_doc_id is automatically resolved!
        self.assertEqual(created["linked_doc_id"], "DOC-2026-001")

        # Check documents table has linked_ledger_id set
        conn = web_server.get_db_conn()
        cur = conn.cursor()
        doc_row = cur.execute("SELECT linked_ledger_id FROM documents WHERE doc_id = 'DOC-2026-001'").fetchone()
        self.assertEqual(doc_row["linked_ledger_id"], created["ledger_id"])
        conn.close()

if __name__ == "__main__":
    unittest.main()
