import os
import sys
import json
import sqlite3
import unittest
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
ROOT_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract_and_build_db
import generate_excel_db
import generate_web_dashboard
import write_all_bats
import parse_all
from db.database_manager import DatabaseManager


def run_dashboard_bat_in_copy(source_dir: Path):
    """01번 배치를 **괄호가 든 임시 폴더 사본**에서 실제 실행한다. (stdout 텍스트, returncode)

    운영 폴더에서 배치를 직접 돌리지 않는다(감사 R4 6.5). 배치와 대시보드 HTML만 복사한다.
    """
    import shutil
    import tempfile
    # 운영 폴더 이름처럼 공백·쉼표·괄호를 섞는다(`10. 국회, 대외기관 … (기획예산팀)`).
    work = Path(tempfile.mkdtemp(prefix="bat run, 국회 (괄호) "))
    try:
        shutil.copy2(source_dir / "01_웹대시보드_실행.bat", work / "01_웹대시보드_실행.bat")
        shutil.copy2(source_dir / "자료요구_통합검색_대시보드.html", work / "자료요구_통합검색_대시보드.html")
        env = os.environ.copy()
        env["DATAREQ_NO_BROWSER"] = "1"
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(work / "01_웹대시보드_실행.bat")],
            cwd=str(work),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout, _ = proc.communicate(input=b"\n\n", timeout=15)
        return stdout.decode("cp949", errors="replace"), proc.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


class TestAuditFixes(unittest.TestCase):
    def test_parse_date_validity(self):
        """Test that date parsing strictly validates calendar dates and rejects garbage numbers."""
        # Valid dates
        self.assertEqual(extract_and_build_db.parse_date("2025.10.15"), "2025-10-15")
        self.assertEqual(extract_and_build_db.parse_date("251015"), "2025-10-15")
        self.assertEqual(extract_and_build_db.parse_date("240801"), "2024-08-01")
        self.assertEqual(extract_and_build_db.parse_date("2026-03-09"), "2026-03-09")
        self.assertEqual(extract_and_build_db.parse_date("2025년 8월 14일"), "2025-08-14")

        # Invalid dates must return "" or not create invalid calendar dates
        self.assertEqual(extract_and_build_db.parse_date("259999"), "")
        self.assertEqual(extract_and_build_db.parse_date("251399"), "")
        self.assertEqual(extract_and_build_db.parse_date("250230"), "")  # Feb 30th invalid
        self.assertEqual(extract_and_build_db.parse_date("000000"), "")
        self.assertEqual(extract_and_build_db.parse_date(""), "")

    def test_extract_metadata_filename_precedence(self):
        """Test that filename year takes precedence over wrong parent directory."""
        # e.g., File named 260901 placed inside 2025 folder
        dummy_path = ROOT_DIR / "2025" / "260901_홍길동_의원_요구자료.hwp.md"
        meta = extract_and_build_db.extract_metadata(dummy_path, md_text="# 질문\n답변")
        self.assertEqual(meta["year"], "2026")
        self.assertEqual(meta["request_date"], "2026-09-01")
        self.assertEqual(meta["requester"], "홍길동 의원")

    def test_split_into_qa_items_filtering(self):
        """Test that list bullet characters and administrative headers are not treated as questions."""
        content = """# 문서 제목
작성일: 2025.10.10
담당부서: 기획예산팀

□ 업무 추진 현황
우리는 다음과 같이 조치함.

* 참고사항
이것은 단순 참고사항입니다.

1. 딥페이크 성범죄 영상물 차단 건수
2024년 10,000건, 2025년 15,000건을 심의 및 차단 조치함.

2. 향후 모니터링 강화 계획
상시 모니터링 인력을 확충할 예정임.
"""
        doc_meta = {
            "doc_id": "DOC-TEST",
            "filename": "test.hwp",
            "year": "2025",
            "request_date": "2025-10-10",
            "requester": "국회",
            "doc_number": "1234",
            "title": "테스트 문서"
        }
        qa_items = extract_and_build_db.split_into_qa_items("DOC-TEST", doc_meta, content)
        
        # '□ 업무 추진 현황' and '* 참고사항' should NOT be extracted as Q&A questions
        titles = [q["question_title"] for q in qa_items]
        self.assertNotIn("□ 업무 추진 현황", titles)
        self.assertNotIn("* 참고사항", titles)
        self.assertTrue(any("딥페이크 성범죄 영상물 차단 건수" in t for t in titles))
        self.assertTrue(any("향후 모니터링 강화 계획" in t for t in titles))

    def test_excel_illegal_characters_sanitization(self):
        """Test openpyxl illegal control character removal."""
        raw_text = "정상 텍스트\x00와 널문자\x08및 백스페이스\x1f끝"
        clean = generate_excel_db.sanitize_text(raw_text)
        self.assertNotIn("\x00", clean)
        self.assertNotIn("\x08", clean)
        self.assertNotIn("\x1f", clean)
        self.assertEqual(clean, "정상 텍스트와 널문자및 백스페이스끝")

    def test_json_script_closing_escape(self):
        """Test that </script> in JSON is safely escaped so it cannot break HTML script tags."""
        sample_json = json.dumps({"content": "</script><script>alert('xss')</script>"}, ensure_ascii=False)
        escaped_json = sample_json.replace("</script>", "<\\/script>")
        self.assertNotIn("</script>", escaped_json)
        self.assertIn("<\\/script>", escaped_json)

    def test_batch_files_valid_utf8(self):
        """Test that all batch files are valid CP949 with chcp 949 without corrupt characters."""
        bat_files = [
            ROOT_DIR / "00_새자료_추가_및_DB동기화.bat",
            ROOT_DIR / "01_웹대시보드_실행.bat",
            ROOT_DIR / "02_웹관리서버_실행.bat",
            ROOT_DIR / "03_엑셀DB_열기.bat",
            ROOT_DIR / "04_부서명_간편설정.bat",
            ROOT_DIR / "05_통합검색프로그램_실행.bat",
            ROOT_DIR / "배포용_자료요구_통합검색시스템" / "00_새자료_추가_및_DB동기화.bat",
            ROOT_DIR / "배포용_자료요구_통합검색시스템" / "01_웹대시보드_실행.bat",
            ROOT_DIR / "배포용_자료요구_통합검색시스템" / "02_웹관리서버_실행.bat",
            ROOT_DIR / "배포용_자료요구_통합검색시스템" / "03_엑셀DB_열기.bat",
            ROOT_DIR / "배포용_자료요구_통합검색시스템" / "05_통합검색프로그램_실행.bat"
        ]
        for bf in bat_files:
            if bf.exists():
                raw = bf.read_bytes()
                # Should decode cleanly with strict cp949
                text = raw.decode("cp949")
                self.assertIn("@echo off", text)
                self.assertIn("chcp 949 >nul", text)
                self.assertNotIn("\ufffd", text)
                self.assertNotIn("占", text)  # Common mojibake artifact
                self.assertNotIn("?쒖썝", text)  # Common UTF-8 read as CP949 artifact

    def test_json_doc_id_uniqueness(self):
        """Test that data_requests.json has unique doc_id for every document."""
        json_path = ROOT_DIR / "data_requests.json"
        if json_path.exists():
            with open(json_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            docs = data.get("documents", [])
            doc_ids = [d["doc_id"] for d in docs]
            self.assertEqual(len(doc_ids), len(set(doc_ids)), "All doc_ids must be strictly unique")

    def test_pii_masking(self):
        """Test that mobile phone numbers and emails are masked while official numbers remain."""
        text = "홍길동 기자(010-1234-5678)의 질의 및 메일 reporter@example.com, 대표전화 02-1234-5678 문의"
        masked = extract_and_build_db.mask_pii(text)
        self.assertNotIn("010-1234-5678", masked)
        self.assertIn("010-****-5678", masked)
        self.assertNotIn("reporter@example.com", masked)
        self.assertIn("re***@example.com", masked)
        self.assertIn("02-1234-5678", masked)  # Official telephone preserved

    def test_sensitive_directory_exclusions(self):
        """Test that sensitive evidence and personnel directories are excluded."""
        self.assertTrue(parse_all.is_excluded_dir("01. 일상 업무자료"))
        self.assertTrue(parse_all.is_excluded_dir("02. 내부 회의록"))
        self.assertTrue(parse_all.is_excluded_dir("04. 모니터링 자료"))
        self.assertTrue(parse_all.is_excluded_dir("06. 서무업무"))
        self.assertTrue(parse_all.is_excluded_dir("★ 업무인수인계_2026"))
        self.assertTrue(parse_all.is_excluded_dir("★ 팀내부_임시보관폴더"))
        self.assertFalse(parse_all.is_excluded_dir("2026"))
        self.assertFalse(parse_all.is_excluded_dir("2025"))

    def test_dashboard_shortcuts_and_o1(self):
        """Test that generate_web_dashboard includes DOC_QAS_MAP and Ctrl+K / ESC shortcuts."""
        template = generate_web_dashboard.HTML_TEMPLATE
        self.assertIn("DOC_QAS_MAP", template)
        self.assertIn("e.key === 'Escape'", template)
        self.assertIn("e.ctrlKey && e.key.toLowerCase() === 'k'", template)

    def test_dashboard_compression_and_size(self):
        """Test that dashboard HTML is compressed under 2MB and decompresses cleanly."""
        import base64
        import gzip
        import re

        html_path = ROOT_DIR / "자료요구_통합검색_대시보드.html"
        if not html_path.exists():
            self.skipTest("운영 대시보드 산출물은 소스 체크아웃에 포함되지 않습니다.")
        
        # 전문 보존이 용량 한도보다 우선. Gzip HTML은 수십 MB까지 허용한다.
        file_size = html_path.stat().st_size
        self.assertLess(file_size, 80 * 1024 * 1024, f"Dashboard HTML should be < 80MB, was {file_size:,} bytes")

        html_text = html_path.read_text(encoding="utf-8")
        self.assertIn('id="COMPRESSED_DATA"', html_text)
        self.assertIn("DecompressionStream", html_text)

        match = re.search(r'<script id="COMPRESSED_DATA" type="text/plain">([\s\S]*?)</script>', html_text)
        self.assertIsNotNone(match, "COMPRESSED_DATA script tag must be found in HTML")
        assert match is not None

        b64_data = match.group(1).strip()
        compressed_bytes = base64.b64decode(b64_data)
        decompressed_bytes = gzip.decompress(compressed_bytes)
        db = json.loads(decompressed_bytes.decode("utf-8"))

        self.assertIn("documents", db)
        self.assertIn("qa_items", db)
        self.assertGreaterEqual(len(db["documents"]), 233)
        self.assertGreaterEqual(len(db["qa_items"]), 1130)
        missing_body = [d.get("doc_id") for d in db["documents"] if not d.get("full_markdown")]
        self.assertEqual(missing_body, [], f"대시보드 문서에 전문이 없음: {missing_body[:8]}")

        # Ensure main JavaScript code is properly enclosed inside <script> tag
        self.assertRegex(html_text, r'</script>\s*<script>\s*let DB\s*=', "Main JS code must be wrapped in <script> tag")

    def test_sqlite_db_optimization_and_pragmas(self):
        """운영 DB가 최적화돼 있고 WAL이 켜져 있으며 크기가 예상 범위인지 본다."""
        db_path = ROOT_DIR / "data_requests.db"
        if not db_path.exists():
            self.skipTest("운영 SQLite 산출물은 소스 체크아웃에 포함되지 않습니다.")

        # 용량 상한은 "어딘가 잘못됐다"를 잡는 canary다. 2026-09-18에 200MB → 400MB로 올렸다:
        # FTS가 엑셀 3만 자 잘림본이 아니라 정본 전문(full_markdown·answer_markdown, 각 약 29MB)을
        # 색인하도록 바꾸면서 trigram 인덱스가 약 114MB 늘었다(제안서 S1, 문서 786건 기준 238MB).
        # 이 선을 넘으면 용량이 어디서 늘었는지부터 본다: python tools/db_report.py
        file_size = db_path.stat().st_size
        self.assertLess(
            file_size, 400 * 1024 * 1024,
            f"DB가 400MB를 넘었습니다({file_size:,} 바이트). "
            f"`python tools/db_report.py`로 어느 테이블·인덱스가 커졌는지 확인하십시오."
        )

        # 운영 DB는 immutable로 연다. mode=ro만으로도 WAL 사이드카(-shm/-wal)가 바뀌어
        # 격리 테스트가 비결정적으로 실패했다. (감사 R4-01)
        from extractors.ledger_parser import connect_readonly
        conn = connect_readonly(db_path, immutable=True)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            self.assertEqual(cur.fetchone()[0], "ok", "DB integrity check must return ok")
        finally:
            conn.close()

        # immutable 연결은 journal_mode를 'delete'로 보고한다. WAL 여부는 파일 헤더로 본다.
        # SQLite 헤더 18·19번째 바이트(쓰기/읽기 형식 버전)가 2이면 WAL 모드다.
        with open(db_path, "rb") as fp:
            header = fp.read(20)
        self.assertEqual((header[18], header[19]), (2, 2), "Journal mode must be WAL")

    def test_sqlite_fts5_external_content_and_triggers(self):
        """Test that FTS5 tables use external content (no _content shadow tables) and triggers keep them in sync."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "fts.db"
            conn = sqlite3.connect(str(db_path))
            DatabaseManager.init_schema(conn)
            conn.execute("INSERT INTO qa_items (qa_id, doc_id, year, request_date, institution, requester, doc_number, q_num, question_title, answer_full, answer_markdown, has_tables, parsed_md_path, original_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("QA-SEED", "DOC-SEED", "2026", "2026-01-01", "국회", "테스트", "1", 1, "텔레그램 질의", "텔레그램 답변", "텔레그램 답변", "N", "", ""))
            conn.commit()
            self._assert_fts_external_content_and_triggers(conn)

    def _assert_fts_external_content_and_triggers(self, conn):
        cur = conn.cursor()

        # 1. External content tables do NOT have _content shadow tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('documents_fts_content', 'qa_items_fts_content')")
        self.assertEqual(len(cur.fetchall()), 0, "External content tables must not have duplicate _content shadow tables")

        # 2. FTS5 queries work with external content
        cur.execute("""
            SELECT q.qa_id, q.question_title 
            FROM qa_items_fts f 
            JOIN qa_items q ON f.rowid = q.rowid 
            WHERE qa_items_fts MATCH '"텔레그램"*' 
            LIMIT 5
        """)
        rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "FTS5 query with external content must return matching rows")

        # 3. Test insert & delete triggers
        cur.execute("BEGIN TRANSACTION")
        test_qa_id = "DOC-TEST-FTS-SYNC"
        cur.execute("""
            INSERT INTO qa_items (qa_id, doc_id, year, request_date, institution, requester, doc_number, q_num, question_title, answer_full, answer_markdown, has_tables, parsed_md_path, original_path)
            VALUES (?, 'DOC-TEST', '2026', '2026-09-08', '국회', '테스트의원', '9999', 1, '테스트질의 동기화검증키워드', '테스트답변 본문내용', '테스트', 'N', '', '')
        """, (test_qa_id,))

        cur.execute("SELECT rowid FROM qa_items_fts WHERE qa_items_fts MATCH '\"동기화검증키워드\"*'")
        match = cur.fetchall()
        self.assertEqual(len(match), 1, "AFTER INSERT trigger must automatically index new row in FTS5")

        cur.execute("DELETE FROM qa_items WHERE qa_id = ?", (test_qa_id,))
        cur.execute("SELECT rowid FROM qa_items_fts WHERE qa_items_fts MATCH '\"동기화검증키워드\"*'")
        match_del = cur.fetchall()
        self.assertEqual(len(match_del), 0, "AFTER DELETE trigger must automatically remove deleted row from FTS5")

        cur.execute("ROLLBACK")
        conn.close()

    def test_composite_indexes_eliminate_filesort(self):
        """Test that composite indexes eliminate USE TEMP B-TREE FOR ORDER BY in query execution plans."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(str(Path(tmp) / "indexes.db"))
            DatabaseManager.init_schema(conn)
            self._assert_composite_indexes(conn)

    def _assert_composite_indexes(self, conn):
        cur = conn.cursor()

        # Query 1: Default Q&A list ordered by date DESC
        cur.execute("EXPLAIN QUERY PLAN SELECT qa_id, request_date, requester, question_title, doc_number FROM qa_items ORDER BY request_date DESC, qa_id ASC LIMIT 500")
        plan1 = [row[3] for row in cur.fetchall()]
        self.assertFalse(any("USE TEMP B-TREE FOR ORDER BY" in p for p in plan1), f"Filesort detected in plan1: {plan1}")
        self.assertTrue(any("idx_qa_date_order" in p for p in plan1), f"idx_qa_date_order not used in plan1: {plan1}")

        # Query 2: Q&A filter by year ordered by date DESC
        cur.execute("EXPLAIN QUERY PLAN SELECT qa_id, request_date, requester, question_title, doc_number FROM qa_items WHERE year = '2026' ORDER BY request_date DESC, qa_id ASC LIMIT 500")
        plan2 = [row[3] for row in cur.fetchall()]
        self.assertFalse(any("USE TEMP B-TREE FOR ORDER BY" in p for p in plan2), f"Filesort detected in plan2: {plan2}")
        self.assertTrue(any("idx_qa_year_date" in p for p in plan2), f"idx_qa_year_date not used in plan2: {plan2}")

        # Query 3: Documents filter by year ordered by date DESC
        cur.execute("EXPLAIN QUERY PLAN SELECT doc_id, request_date, requester, title, doc_number FROM documents WHERE year = '2026' ORDER BY request_date DESC, doc_id ASC LIMIT 500")
        plan3 = [row[3] for row in cur.fetchall()]
        self.assertFalse(any("USE TEMP B-TREE FOR ORDER BY" in p for p in plan3), f"Filesort detected in plan3: {plan3}")
        self.assertTrue(any("idx_doc_year_date" in p for p in plan3), f"idx_doc_year_date not used in plan3: {plan3}")

        conn.close()

    def test_launcher_gui_fts_and_caching(self):
        """Test launcher GUI helper clean_fts_query and dashboard DOCS_MAP/QA_MAP."""
        import launcher_gui

        self.assertEqual(launcher_gui.clean_fts_query("딥페이크 텔레그램"), '"딥페이크" AND "텔레그램"')
        # `!`·`*` 같은 FTS 예약문자를 떼어내는지 본다. 2글자 낱말(의원)이 섞이면 trigram으로
        # 표현할 수 없어 빈 문자열이 되므로, 정리 동작만 보려면 3글자 이상 낱말을 쓴다.
        self.assertEqual(launcher_gui.clean_fts_query("홍길동! 시정요구*"), '"홍길동" AND "시정요구"')
        self.assertEqual(launcher_gui.clean_fts_query("홍길동! 의원*"), "")
        self.assertEqual(launcher_gui.clean_fts_query(""), "")

        template = generate_web_dashboard.HTML_TEMPLATE
        self.assertIn("DOCS_MAP", template)
        self.assertIn("QA_MAP", template)

    def test_schema_consistency_across_modules(self):
        """Test that schemas in extract_and_build_db, web_server.init_db, and build_universal_package match 100%."""
        import tempfile
        import shutil
        import web_server
        import build_universal_package

        temp_dir = tempfile.mkdtemp()
        try:
            db_server = Path(temp_dir) / "server.db"
            db_pkg = Path(temp_dir) / "pkg.db"

            # 1. Initialize DB with web_server.init_db()
            orig_server_db = web_server.DB_PATH
            orig_base_dir = web_server.BASE_DIR
            web_server.DB_PATH = db_server
            # init_db()는 시작 동기화(보류 큐 반영 + 엑셀→DB)까지 한다. BASE_DIR를 바꾸지 않으면
            # 운영 마스터 엑셀과 보류 큐를 대상으로 실행된다. (감사 R3-01)
            web_server.BASE_DIR = Path(temp_dir)
            try:
                web_server.init_db()
            finally:
                web_server.DB_PATH = orig_server_db
                web_server.BASE_DIR = orig_base_dir

            # 2. Initialize DB with build_universal_package.build_clean_database()
            pkg_dir = Path(temp_dir) / "pkg"
            pkg_dir.mkdir()
            build_universal_package.build_clean_database(pkg_dir)
            db_pkg = pkg_dir / "data_requests.db"

            # 3. Fresh production-schema fixture; source checkouts do not track
            # real operational data_requests.db files.
            prod_db = Path(temp_dir) / "production_schema.db"
            conn = sqlite3.connect(str(prod_db))
            DatabaseManager.init_schema(conn)
            conn.close()

            def get_schema_columns(db_file):
                conn = sqlite3.connect(str(db_file))
                cur = conn.cursor()
                tables = ["documents", "qa_items", "request_ledger"]
                schema_info = {}
                for t in tables:
                    cols = [row[1] for row in cur.execute(f"PRAGMA table_info({t})").fetchall()]
                    schema_info[t] = cols
                fts_tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('documents_fts', 'qa_items_fts', 'request_ledger_fts')").fetchall()]
                schema_info["fts"] = sorted(fts_tables)
                conn.close()
                return schema_info

            schema_prod = get_schema_columns(prod_db)
            schema_server = get_schema_columns(db_server)
            schema_pkg = get_schema_columns(db_pkg)

            # Assert columns match across all three
            for t in ["documents", "qa_items", "request_ledger"]:
                self.assertEqual(schema_server[t], schema_prod[t], f"Table {t} columns mismatch between web_server and prod")
                self.assertEqual(schema_pkg[t], schema_prod[t], f"Table {t} columns mismatch between build_package and prod")

            # Check critical qa_items column names
            self.assertIn("question_title", schema_server["qa_items"])
            self.assertIn("answer_full", schema_server["qa_items"])
            self.assertIn("answer_markdown", schema_server["qa_items"])
            self.assertIn("parsed_md_path", schema_server["qa_items"])

            # Check FTS5 virtual tables presence in all three
            expected_fts = ["documents_fts", "qa_items_fts", "request_ledger_fts"]
            for fts in expected_fts:
                self.assertIn(fts, schema_server["fts"])
                self.assertIn(fts, schema_pkg["fts"])
                self.assertIn(fts, schema_prod["fts"])

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_batch_files_parentheses_directory_safe(self):
        """Verify that batch files do not contain syntax blocks vulnerable to parentheses in path (e.g. %CD% in parens) and execute safely."""
        bat_files = [
            ROOT_DIR / "00_새자료_추가_및_DB동기화.bat",
            ROOT_DIR / "01_웹대시보드_실행.bat",
            ROOT_DIR / "02_웹관리서버_실행.bat",
            ROOT_DIR / "03_엑셀DB_열기.bat",
            ROOT_DIR / "04_부서명_간편설정.bat",
            ROOT_DIR / "05_통합검색프로그램_실행.bat"
        ]
        for bf in bat_files:
            if bf.exists():
                text = bf.read_text(encoding="cp949")
                # Ensure no %CD% inside parenthesis blocks
                self.assertNotIn("%CD%", text, f"%CD% found in {bf.name}, can break CMD parser in folders with ()")
                # Ensure goto error handling structure is used
                if "if not exist" in text:
                    self.assertIn("goto", text, f"{bf.name} should use goto for safe error handling")

        # A source checkout deliberately has no generated dashboard.  The launch
        # behavior itself is exercised only when that deployment artifact exists.
        if not (ROOT_DIR / "자료요구_통합검색_대시보드.html").exists():
            return
        # Test actual execution of 01_웹대시보드_실행.bat in a directory containing parentheses
        # (운영 폴더가 아니라 임시 사본에서 실행한다. 상대 경로로 넘기면 일부 Windows 환경의 cmd.exe가
        # 한글 배치 파일명을 찾지 못하므로 절대 경로로 호출한다.)
        out_text, returncode = run_dashboard_bat_in_copy(ROOT_DIR)
        self.assertEqual(returncode, 0, f"01_웹대시보드_실행.bat failed with returncode {returncode}")
        self.assertNotIn("[오류]", out_text)
        self.assertIn("웹 브라우저를 실행합니다", out_text)

    def test_all_batch_files_across_all_packages(self):
        """Recursively test all batch files across main, distribution, and universal distribution packages."""
        folders = [
            ROOT_DIR,
            ROOT_DIR / "배포용_자료요구_통합검색시스템",
            ROOT_DIR.parent / "국회자료요구_스마트시스템_범용배포용"
        ]
        total_bats_checked = 0
        for fld in folders:
            if not fld.exists():
                continue
            for bat_file in fld.glob("*.bat"):
                total_bats_checked += 1
                text = bat_file.read_text(encoding="cp949")
                # Rule validator must pass without exception
                write_all_bats.validate_batch_file_rules(text, bat_file.name)
                
                # If it is 01_웹대시보드_실행.bat, test actual execution
                if bat_file.name == "01_웹대시보드_실행.bat" and (fld / "자료요구_통합검색_대시보드.html").exists():
                    out, returncode = run_dashboard_bat_in_copy(fld)
                    self.assertEqual(returncode, 0, f"{bat_file} failed with returncode {returncode}")
                    self.assertIn("웹 브라우저를 실행합니다", out)

        # A clean source checkout contains the seven primary launchers; generated
        # distribution folders add more only after an explicit packaging run.
        self.assertGreaterEqual(total_bats_checked, 7, f"Expected at least 7 primary batch files, checked {total_bats_checked}")


class TestRepairedLinksJsonRefresh(unittest.TestCase):
    """링크 복구로 바뀐 값은 같은 실행의 JSON 캐시에도 들어가야 한다. (감사 R6-03)

    예전에는 JSON을 먼저 쓰고 복구를 뒤에 해서, 끊어진 연결이 있던 실행의
    파생 산출물(통합 XLSX·대시보드 HTML) 연결 표시가 한 사이클 늦었다.
    """
    def test_repaired_links_are_reflected_in_json(self):
        import shutil
        import tempfile
        from db.database_manager import DatabaseManager
        from services.ledger.autolink import repair_broken_links
        tmp = Path(tempfile.mkdtemp(prefix="r6_03_"))
        try:
            db = tmp / "t.db"
            conn = DatabaseManager.get_connection(db)
            try:
                DatabaseManager.ensure_schema(conn)
                conn.execute("INSERT INTO documents (doc_id, year, request_date, title, full_markdown) "
                             "VALUES (?, ?, ?, ?, ?)",
                             ("D1", "2026", "2026-01-01", "제목", "본문"))
                conn.execute("INSERT INTO request_ledger (ledger_id, year, title, requester, "
                             "linked_doc_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             ("REQ-2026-001", "2026", "제목", "의원",
                              "D-GONE", "2026-01-01 00:00:00", "2026-01-01 00:00:00"))
                conn.commit()
            finally:
                conn.close()
            combined = {
                "documents": [{"doc_id": "D1", "linked_ledger_id": ""}],
                "qa_items": [],
                "request_ledger": [{"ledger_id": "REQ-2026-001",
                                    "linked_doc_id": "D-GONE"}],
            }
            fix_conn = DatabaseManager.get_connection(db)
            try:
                fix = repair_broken_links(fix_conn)
            finally:
                fix_conn.close()
            self.assertEqual(fix.get("checked"), 1, "픽스처 전제: 끊어진 연결 1건")
            patched = extract_and_build_db.refresh_repaired_links(db, combined)
            jp = tmp / "d.json"
            extract_and_build_db.write_json_atomic(jp, combined)
            back = json.loads(jp.read_text(encoding="utf-8"))
            db_conn = DatabaseManager.get_connection(db)
            try:
                db_led = db_conn.execute(
                    "SELECT linked_doc_id FROM request_ledger WHERE ledger_id='REQ-2026-001'"
                ).fetchone()[0]
            finally:
                db_conn.close()
            self.assertEqual(back["request_ledger"][0]["linked_doc_id"], db_led or "",
                             "JSON 연결이 복구 후 DB 값과 같아야 한다")
            self.assertGreaterEqual(patched, 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


