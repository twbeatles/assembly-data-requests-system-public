# -*- coding: utf-8 -*-
"""검색·DB 고도화(제안서 S1~S10, D1~D9)와 대시보드 페이로드 경량화 회귀 테스트.

소스 문자열 grep으로 검증하지 않는다(테스트 규칙). 실제 DB를 만들어 질의하고,
대시보드 JS는 템플릿에서 떼어 Node로 실행한다.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db import fts_spec  # noqa: E402
from db import migrations  # noqa: E402
from db.database_manager import DatabaseManager  # noqa: E402
from db.health import check_database  # noqa: E402
from search_query import (  # noqa: E402
    build_fts_match,
    fields_match,
    normalize_date,
    parse_query,
    row_matches,
    score_row,
)
from services.ledger.service import LedgerService  # noqa: E402
from template_renderer import DashboardRenderer  # noqa: E402

# 본문 뒤쪽에만 있는 낱말. 예전에는 엑셀 3만 자 잘림본만 색인해 이런 낱말을 놓쳤다.
TAIL_WORD = "심층분석보고"
LONG_BODY = "# 전문\n\n" + ("가나다라마바사 " * 400) + f"\n\n{TAIL_WORD} 결과를 첨부함."
TRUNCATED = LONG_BODY[:120]


def _seed(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        DatabaseManager.ensure_schema(conn)
        conn.execute(
            "INSERT INTO documents (doc_id, year, request_date, institution, requester, doc_number, "
            "title, question_list, answer_summary, answer_full_text, full_markdown, has_tables) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("D1", "2026", "2026-03-05", "국회", "김철수 의원", "0517",
             "딥페이크 대응 현황", "1. 접속차단 현황", "요약", TRUNCATED, LONG_BODY, "N"),
        )
        conn.execute(
            "INSERT INTO documents (doc_id, year, request_date, institution, requester, doc_number, "
            "title, question_list, answer_summary, answer_full_text, full_markdown, has_tables) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("D2", "2025", "2025-06-10", "대외기관", "홍길동 의원", "0831",
             "국회 자료요구 통계", "2. 통계", "요약", "짧은 본문", "# 전문\n\n국회 관련 통계 자료", "N"),
        )
        conn.execute(
            "INSERT INTO qa_items (qa_id, doc_id, year, request_date, requester, q_num, "
            "question_title, answer_full, answer_markdown, has_tables) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("D1-Q1", "D1", "2026", "2026-03-05", "김철수 의원", 1,
             "접속차단 실적", TRUNCATED, LONG_BODY, "N"),
        )
        conn.execute(
            "INSERT INTO request_ledger (ledger_id, year, seq_no, party, requester, title, details, "
            "request_date, deadline, submit_date, status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("REQ-2026-001", "2026", "17", "일반정당", "김철수 의원", "딥페이크 시정요구 내역",
             "최근 3개년 조치 현황", "2026-03-05", "2026-03-09", "2026-03-08", "제출", ""),
        )
        conn.commit()
    finally:
        conn.close()


class SearchUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="search_upgrade_")
        cls.base = Path(cls.tmp)
        cls.db = cls.base / "data_requests.db"
        _seed(cls.db)
        cls.svc = LedgerService(db_path=cls.db, json_path=cls.base / "d.json", base_dir=cls.base)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --- S1: 정본 전문 색인 ---------------------------------------------------
    def test_fts_indexes_canonical_body_not_excel_truncation(self):
        """FTS 컬럼은 정본이어야 한다. 잘림본을 색인하면 본문 뒷부분을 통째로 놓친다."""
        self.assertIn("full_markdown", fts_spec.FTS_SPECS["documents_fts"][1])
        self.assertNotIn("answer_full_text", fts_spec.FTS_SPECS["documents_fts"][1])
        self.assertIn("answer_markdown", fts_spec.FTS_SPECS["qa_items_fts"][1])
        self.assertNotIn("answer_full", fts_spec.FTS_SPECS["qa_items_fts"][1])

    def test_word_only_in_body_tail_is_found(self):
        """본문 끝에만 있는 낱말이 검색된다. 이 테스트가 S1 결함의 직접 재현이다."""
        self.assertNotIn(TAIL_WORD, TRUNCATED, "픽스처 전제: 잘림본에는 그 낱말이 없다")
        docs = self.svc.query_documents(kw=TAIL_WORD, slim=True, use_synonyms=False)
        self.assertEqual([d["doc_id"] for d in docs], ["D1"])
        qas = self.svc.query_qa_items(kw=TAIL_WORD, slim=True, use_synonyms=False)
        self.assertEqual([q["qa_id"] for q in qas], ["D1-Q1"])

    # --- S2: 2글자 질의 ------------------------------------------------------
    def test_two_char_query_is_not_expressed_in_fts(self):
        self.assertEqual(build_fts_match("국회"), "")
        self.assertEqual(build_fts_match("딥페이크 국회"), "",
                         "2글자가 섞이면 전체를 대체 경로로 보낸다")
        self.assertNotEqual(build_fts_match("딥페이크"), "")

    def test_two_char_query_still_returns_rows(self):
        """FTS로 표현할 수 없어도 결과는 나와야 한다. 대체 경로가 책임진다."""
        docs = self.svc.query_documents(kw="국회", slim=True, use_synonyms=False)
        self.assertIn("D2", [d["doc_id"] for d in docs])

    def test_prefilter_keeps_negation_results(self):
        """제외어가 있어도 선필터가 결과를 줄이면 안 된다."""
        with_neg = self.svc.query_documents(kw="국회 -딥페이크", slim=True, use_synonyms=False)
        ids = [d["doc_id"] for d in with_neg]
        self.assertIn("D2", ids)
        self.assertNotIn("D1", ids)

    def test_prefilter_matches_python_evaluation(self):
        """선필터를 켠 결과와 파이썬 전수 평가 결과가 같아야 한다.

        **동의어를 켠 경우를 반드시 함께 본다.** 동의어는 API 기본값이 '켬'인데, 예전에는
        동의어가 켜지면 선필터를 통째로 껐다가(느림), 다시 켰을 때는 공백 제거 비교를
        짧은 필드에만 걸어 본문에서 갈라진 낱말을 놓쳤다(운영 질의가 72건→70건).
        """
        for kw in ("국회", "통계", "딥페이크 현황", "국회 OR 딥페이크", "딥페이크 -통계"):
            for use_syn in (False, True):
                with self.subTest(kw=kw, synonyms=use_syn):
                    narrowed = {d["doc_id"] for d in
                                self.svc.query_documents(kw=kw, slim=True, use_synonyms=use_syn)}
                    plan = parse_query(kw)
                    conn = sqlite3.connect(str(self.db))
                    conn.row_factory = sqlite3.Row
                    try:
                        fields = fts_spec.FTS_SPECS["documents_fts"][1]
                        cho = fts_spec.FTS_CHOSEONG_FIELDS["documents_fts"]
                        brute = {
                            r["doc_id"] for r in conn.execute("SELECT * FROM documents")
                            if row_matches(plan, [r[f] for f in fields],
                                           [r[f] for f in cho], use_syn)
                        }
                    finally:
                        conn.close()
                    self.assertEqual(narrowed, brute, f"선필터가 결과를 바꿨다: {kw} (동의어={use_syn})")

    def test_prefilter_finds_terms_split_by_whitespace_in_body(self):
        """본문에서 공백으로 갈라진 낱말도 찾아야 한다.

        파이썬 평가는 띄어쓰기를 무시하므로 `국 회`가 든 본문도 `국회` 질의에 맞는다.
        선필터가 원시 LIKE만 걸면 그런 행이 통째로 사라진다.
        """
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute(
                "INSERT INTO documents (doc_id, year, request_date, title, full_markdown) "
                "VALUES ('D-SPLIT', '2026', '2026-05-05', '띄어쓰기 시험', "
                "'앞부분\\n\\n국 회\\t자료요구 관련 내용\\n\\n뒷부분')"
            )
            conn.commit()
        finally:
            conn.close()
        try:
            for use_syn in (False, True):
                with self.subTest(synonyms=use_syn):
                    ids = [d["doc_id"] for d in
                           self.svc.query_documents(kw="국회", slim=True, use_synonyms=use_syn)]
                    self.assertIn("D-SPLIT", ids, "본문에서 공백으로 갈라진 낱말을 놓쳤다")
        finally:
            conn = sqlite3.connect(str(self.db))
            try:
                conn.execute("DELETE FROM documents WHERE doc_id='D-SPLIT'")
                conn.commit()
            finally:
                conn.close()

    def test_prefilter_matches_nonstandard_whitespace(self):
        """선필터 블롭과 파이썬 평가가 같은 공백 집합을 봐야 한다. (감사 R6-01)

        `_norm_expr`가 공백 4종(공백·TAB·LF·CR)만 떼던 시절에는 NBSP·수직탭·폼피드·
        전각공백으로 갈라진 낱말이 선필터 LIKE에서 잘려 검색에서 빠졌다.
        """
        splits = {
            "D-WS-NBSP": "앞부분 국\xa0회 뒷부분",
            "D-WS-VT": "앞부분 국\x0b회 뒷부분",
            "D-WS-FF": "앞부분 국\x0c회 뒷부분",
            "D-WS-IDSP": "앞부분 국\u3000회 뒷부분",
        }
        conn = sqlite3.connect(str(self.db))
        try:
            for doc_id, body in splits.items():
                conn.execute(
                    "INSERT INTO documents (doc_id, year, request_date, title, full_markdown) "
                    "VALUES (?, '2026', '2026-05-05', '공백 변형 시험', ?)",
                    (doc_id, body),
                )
            conn.commit()
        finally:
            conn.close()
        try:
            for use_syn in (False, True):
                with self.subTest(synonyms=use_syn):
                    ids = [d["doc_id"] for d in
                           self.svc.query_documents(kw="국회", slim=True,
                                                    use_synonyms=use_syn)]
                    for doc_id in splits:
                        self.assertIn(doc_id, ids,
                                        f"비표준 공백으로 갈라진 낱말을 놓쳤다: {doc_id}")
        finally:
            conn = sqlite3.connect(str(self.db))
            try:
                conn.execute("DELETE FROM documents WHERE doc_id IN (?,?,?,?)",
                             tuple(splits))
                conn.commit()
            finally:
                conn.close()

    # --- S3: 두 경로가 같은 필드를 본다 --------------------------------------
    def test_fts_columns_and_scan_fields_agree(self):
        for kind in ("docs", "qa", "ledger"):
            spec, fts_name, text_fields, cho_fields = LedgerService._spec(kind)
            self.assertEqual(tuple(text_fields), fts_spec.FTS_SPECS[fts_name][1])
            for f in cho_fields:
                self.assertIn(f, text_fields, f"{kind}: 초성 필드는 검색 필드의 부분집합이어야 한다")
            self.assertEqual(len(fts_spec.FTS_WEIGHTS[fts_name]), len(text_fields),
                             f"{fts_name}: bm25 가중치 개수가 컬럼 수와 달라 검색이 실패한다")

    # --- S4: 동의어 ----------------------------------------------------------
    def test_synonyms_shared_between_server_and_dashboard(self):
        import search_synonyms
        self.assertIn("딥페이크", search_synonyms.SYNONYMS)
        self.assertIn("허위영상물", search_synonyms.expand("딥페이크"))
        assembled = DashboardRenderer().load_template()
        self.assertNotIn("__SYNONYMS_JSON__", assembled, "조립본에 자리표시자가 남았다")
        self.assertIn('"허위영상물"', assembled)

    def test_synonym_expansion_widens_server_results(self):
        no_syn = self.svc.query_documents(kw="허위영상물", slim=True, use_synonyms=False)
        with_syn = self.svc.query_documents(kw="허위영상물", slim=True, use_synonyms=True)
        self.assertEqual(no_syn, [])
        self.assertIn("D1", [d["doc_id"] for d in with_syn],
                      "동의어를 켜면 '딥페이크' 문서가 나와야 한다")

    # --- S5: 스니펫 ----------------------------------------------------------
    def test_snippet_is_returned_for_both_paths(self):
        for kw in ("딥페이크", "국회"):  # FTS 경로 / 대체 경로
            with self.subTest(kw=kw):
                res = self.svc.search_all(kw=kw, slim=True, use_synonyms=False, with_snippet=True)
                found = res["documents"] + res["qa_items"] + res["ledger"]
                self.assertTrue(found, f"{kw}: 결과가 있어야 스니펫을 볼 수 있다")
                self.assertTrue(all("snippet" in r for r in found),
                                "경로에 따라 스니펫이 있었다 없었다 하면 안 된다")

    # --- S6: 필드 한정·기간 --------------------------------------------------
    def test_field_qualifiers(self):
        plan = parse_query("요구자:홍길동 상태:제출 기간:2025-01-01..2025-12-31")
        self.assertEqual(plan["fields"]["requester"], ["홍길동"])
        self.assertEqual(plan["fields"]["status"], ["제출"])
        self.assertEqual(plan["fields"]["date_from"], "2025-01-01")
        self.assertEqual(plan["fields"]["date_to"], "2025-12-31")
        self.assertEqual(plan["groups"], [], "한정자는 일반 검색어로 새지 않는다")

    def test_unknown_prefix_is_not_a_field(self):
        """모르는 접두사는 한정자가 아니다. 콜론이 든 검색어가 사라지면 안 된다."""
        plan = parse_query("테스트:질의")
        self.assertEqual(plan["fields"], {})
        self.assertTrue(plan["groups"])

    def test_date_range_filters_rows(self):
        rows = self.svc.query_documents(kw="기간:2026-01-01..2026-12-31", slim=True)
        self.assertEqual([d["doc_id"] for d in rows], ["D1"])
        rows = self.svc.query_documents(kw="요구자:홍길동", slim=True)
        self.assertEqual([d["doc_id"] for d in rows], ["D2"])

    def test_normalize_date_expands_partial_values(self):
        self.assertEqual(normalize_date("2026"), "2026-01-01")
        self.assertEqual(normalize_date("2026", end_of_range=True), "2026-12-31")
        self.assertEqual(normalize_date("2026-02", end_of_range=True), "2026-28"[:4] + "-02-28")

    def test_february_end_covers_leap_day(self):
        """윤년 2월은 29일까지가 범위다. (감사 R6-05)

        월 끝일 고정 표가 2월을 무조건 28일로 둬서 `기간:2024-02`가
        02-29일자 문서를 놓쳤다.
        """
        self.assertEqual(normalize_date("2024-02", end_of_range=True), "2024-02-29")
        self.assertEqual(normalize_date("2023-02", end_of_range=True), "2023-02-28")
        plan = parse_query("기간:2024-02")
        self.assertTrue(fields_match(plan, {"request_date": "2024-02-29"}))
        self.assertFalse(fields_match(plan, {"request_date": "2024-03-01"}))
        plan = parse_query("기간:2023-02")
        self.assertFalse(fields_match(plan, {"request_date": "2024-02-29"}))

    def test_fields_match_on_plain_dict(self):
        plan = parse_query("상태:제출")
        self.assertTrue(fields_match(plan, {"status": "제출"}))
        self.assertFalse(fields_match(plan, {"status": "미제출"}))

    # --- S7: 관련도 ----------------------------------------------------------
    def test_fallback_scores_title_above_body(self):
        plan = parse_query("국회")
        title_hit = score_row(plan, [("국회 자료요구 통계", 50)])
        body_hit = score_row(plan, [("국회 관련 통계 자료", 10)])
        self.assertGreater(title_hit, body_hit)

    def test_score_does_not_choseong_convert_long_bodies(self):
        """초성 토큰이 없으면 본문 초성 변환을 하지 않는다(한 번에 수만 자를 돈다)."""
        plan = parse_query("국회")
        huge = "가" * 200000
        import time
        started = time.time()
        score_row(plan, [(huge, 10)])
        self.assertLess(time.time() - started, 1.0, "본문 초성 변환이 되살아났다")

    # --- S8: 자동완성 --------------------------------------------------------
    def test_suggest_returns_requesters(self):
        items = self.svc.suggest("김철")
        self.assertTrue(any(i["value"] == "김철수 의원" for i in items))
        self.assertEqual(self.svc.suggest(""), [])

    # --- S9: 검색 로그 -------------------------------------------------------
    def test_zero_result_queries_are_logged(self):
        self.svc.query_documents(kw="존재하지않는낱말xyz", slim=True, use_synonyms=False)
        insights = self.svc.search_insights()
        self.assertTrue(any(r["query"] == "존재하지않는낱말xyz" for r in insights["zero_result"]))


class DatabaseUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db_upgrade_")
        self.base = Path(self.tmp)
        self.db = self.base / "data_requests.db"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- D1: 스키마 버전 ------------------------------------------------------
    def test_fresh_database_is_stamped_with_current_version(self):
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(migrations.get_version(conn), migrations.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_legacy_database_is_migrated_not_silently_used(self):
        """구 스키마(v0 + 옛 FTS 컬럼)를 열면 정본 컬럼으로 올라와야 한다."""
        conn = sqlite3.connect(str(self.db))
        try:
            DatabaseManager.ensure_schema(conn)
            # 구 버전 흉내: 버전을 0으로 되돌리고 FTS를 옛 컬럼 구성으로 바꾼다.
            for stmt in fts_spec.drop_sqls():
                conn.execute(stmt)
            conn.execute("""CREATE VIRTUAL TABLE documents_fts USING fts5(
                title, requester, question_list, answer_full_text,
                content='documents', content_rowid='rowid', tokenize='trigram')""")
            migrations.set_version(conn, 0)
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(str(self.db))
        try:
            DatabaseManager.ensure_schema(conn)
            self.assertEqual(migrations.get_version(conn), migrations.SCHEMA_VERSION)
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='documents_fts'"
            ).fetchone()[0]
            self.assertIn("full_markdown", sql)
            self.assertNotIn("answer_full_text", sql)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger_history)")}
            self.assertIn("changed_fields", cols)
            self.assertIn("actor", cols)
        finally:
            conn.close()

    def test_fresh_schema_matches_migrated_schema(self):
        """새로 만든 DB와 올린 DB의 스키마가 같아야 한다.

        마이그레이션에만 컬럼을 넣고 기본 `CREATE TABLE`에 넣지 않으면, 새로 만든 DB에는
        그 컬럼이 영영 생기지 않는다(마이그레이션은 기존 DB를 올릴 때만 돈다).
        실제로 `ledger_history.changed_fields`·`actor`가 그 상태였다.
        """
        fresh = self.base / "fresh.db"
        _seed(fresh)

        legacy = self.base / "legacy.db"
        _seed(legacy)
        conn = sqlite3.connect(str(legacy))
        try:
            # 구 스키마 흉내: v2에서 더한 것들을 되돌린다.
            conn.execute("DROP TABLE ledger_history")
            conn.execute("""CREATE TABLE ledger_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ledger_id TEXT, action TEXT, changed_at TEXT, snapshot TEXT)""")
            migrations.set_version(conn, 0)
            conn.commit()
        finally:
            conn.close()
        conn = sqlite3.connect(str(legacy))
        try:
            DatabaseManager.ensure_schema(conn)
        finally:
            conn.close()

        def _schema(path):
            c = sqlite3.connect(str(path))
            try:
                tables = sorted(r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
                return {t: sorted(r[1] for r in c.execute(f"PRAGMA table_info({t})")) for t in tables}
            finally:
                c.close()

        self.assertEqual(_schema(fresh), _schema(legacy),
                         "새 DB와 마이그레이션한 DB의 스키마가 갈라졌다")

    def test_fresh_database_is_not_reported_as_migrated(self):
        """새로 만든 DB에 마이그레이션이 돌면 안 된다.

        `ensure_schema`는 테이블을 만든 뒤 마이그레이션을 부르므로, 그 시점에 신규 여부를
        판정하면 새 DB도 늘 '기존 DB'가 된다. 파이프라인이 매번 빈 테이블에 마이그레이션을
        돌리고 로그를 찍어, 진짜 마이그레이션이 일어난 순간이 묻혔다.
        """
        fresh = self.base / "quiet.db"
        conn = sqlite3.connect(str(fresh))
        try:
            DatabaseManager.ensure_schema(conn)
            result = migrations.apply_migrations(conn)
            self.assertEqual(result["applied"], [], "새 DB에 마이그레이션 단계가 돌았다")
            self.assertEqual(migrations.get_version(conn), migrations.SCHEMA_VERSION)
        finally:
            conn.close()

    def test_newer_schema_is_refused(self):
        """구 버전 프로그램이 신 DB를 조용히 쓰면 새 컬럼을 지울 수 있다."""
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            migrations.set_version(conn, migrations.SCHEMA_VERSION + 5)
            conn.commit()
            with self.assertRaises(migrations.SchemaTooNewError):
                migrations.apply_migrations(conn)
        finally:
            conn.close()

    # --- D2: 정합성 점검 ------------------------------------------------------
    def test_health_check_passes_on_fresh_database(self):
        _seed(self.db)
        report = check_database(self.db)
        self.assertTrue(report["success"], report["problems"])
        for name, entry in report["fts"].items():
            self.assertTrue(entry["ok"], f"{name} 인덱스 행수 불일치")

    def test_health_check_detects_stale_fts_index(self):
        """트리거를 우회해 본체만 바꾸면 인덱스가 어긋난다. 조용히 넘어가면 안 된다."""
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            for name in fts_spec.fts_trigger_names():
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(
                "INSERT INTO documents (doc_id, year, title, full_markdown) VALUES ('D9','2026','유령','x')")
            conn.commit()
        finally:
            conn.close()
        report = check_database(self.db)
        self.assertFalse(report["success"])
        self.assertTrue(any("documents_fts" in p for p in report["problems"]), report["problems"])

    def test_health_check_reports_dangling_links(self):
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute("UPDATE request_ledger SET linked_doc_id = 'NOPE' WHERE ledger_id='REQ-2026-001'")
            conn.commit()
        finally:
            conn.close()
        report = check_database(self.db)
        self.assertEqual(report["links"]["dangling_linked_doc_id"], 1)

    def test_repair_relinks_or_clears_broken_links(self):
        """끊어진 연결은 다시 잇거나 비운다. 값이 **DB에 저장돼야** 한다.

        `auto_link_ledger_to_doc`은 문서 쪽 역참조만 쓰고 대장 쪽 값은 호출자가 저장하도록
        되어 있다. 복구 함수가 그걸 빠뜨려, 처음에는 '재연결 2건'을 보고하고도 DB는
        그대로였다.
        """
        from services.ledger.autolink import repair_broken_links
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            # 이 대장 항목은 요구자·요구일이 D1과 같아 다시 이어질 수 있다.
            conn.execute("UPDATE request_ledger SET linked_doc_id='GONE' WHERE ledger_id='REQ-2026-001'")
            # 다시 이을 근거가 없는 항목 하나를 더 만든다.
            conn.execute(
                "INSERT INTO request_ledger (ledger_id, year, seq_no, requester, title, "
                "request_date, deadline, status, linked_doc_id) "
                "VALUES ('REQ-2026-999','2026','99','없는사람','근거 없음','2019-01-01','2019-01-02','제출','ALSO-GONE')"
            )
            conn.commit()
            self.assertEqual(check_database(self.db)["links"]["dangling_linked_doc_id"], 2)

            out = repair_broken_links(conn)
            self.assertEqual(out["checked"], 2)
            self.assertEqual(out["relinked"] + out["cleared"], 2)

            rows = {r["ledger_id"]: r["linked_doc_id"] for r in
                    conn.execute("SELECT ledger_id, linked_doc_id FROM request_ledger")}
            self.assertEqual(rows["REQ-2026-001"], "D1", "재연결 값이 DB에 저장되지 않았다")
            self.assertEqual(rows["REQ-2026-999"], "", "이을 수 없으면 값을 비워야 한다")
        finally:
            conn.close()
        self.assertEqual(check_database(self.db)["links"]["dangling_linked_doc_id"], 0)

    def test_undated_documents_are_a_note_not_a_warning(self):
        """원문에 날짜가 없는 문서는 고칠 수 없다. 경고로 띄우면 진짜 경고가 묻힌다."""
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute(
                "INSERT INTO documents (doc_id, title, full_markdown) VALUES ('D-UNDATED','날짜없음','본문')")
            conn.commit()
        finally:
            conn.close()
        report = check_database(self.db)
        self.assertTrue(any("날짜가 없어" in n for n in report["notes"]), report["notes"])
        self.assertFalse([w for w in report["warnings"] if "연도" in w],
                         f"고칠 수 없는 항목이 경고로 올라왔다: {report['warnings']}")
        self.assertTrue(report["success"])

    def test_dated_document_missing_year_is_a_warning(self):
        """요구일자가 있는데 연도가 비면 그건 추출 결함이라 경고해야 한다."""
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute(
                "INSERT INTO documents (doc_id, request_date, title, full_markdown) "
                "VALUES ('D-DATED','2026-04-01','연도누락','본문')")
            conn.commit()
        finally:
            conn.close()
        report = check_database(self.db)
        self.assertEqual(report["quality"]["documents_without_year_but_dated"], 1)
        self.assertTrue([w for w in report["warnings"] if "연도" in w], report["warnings"])

    # --- D4: 집계·인덱스 ------------------------------------------------------
    def test_ledger_summary_counts_without_loading_bodies(self):
        _seed(self.db)
        svc = LedgerService(db_path=self.db, json_path=self.base / "d.json", base_dir=self.base)
        summary = svc.ledger_summary()
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["by_status"], {"제출": 1})

    def test_deadline_index_exists(self):
        _seed(self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        finally:
            conn.close()
        self.assertIn("idx_ledger_deadline", names)

    # --- D7: 이력 필드 --------------------------------------------------------
    def test_update_history_records_changed_fields(self):
        _seed(self.db)
        svc = LedgerService(db_path=self.db, json_path=self.base / "d.json", base_dir=self.base)
        svc.update_ledger_item("REQ-2026-001", {"note": "확인함"})
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT changed_fields, actor FROM ledger_history "
                "WHERE action='UPDATE' ORDER BY history_id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertIn("note", row["changed_fields"])

    # --- D5: 백업 -------------------------------------------------------------
    def test_snapshot_and_history_dump(self):
        _seed(self.db)
        from db import backup as db_backup
        out = db_backup.run_all(self.db, self.base)
        self.assertTrue(out["database"]["success"], out["database"])
        self.assertTrue(Path(out["database"]["path"]).exists())
        self.assertTrue(out["history"]["success"], out["history"])
        # 같은 날 다시 불러도 실패하지 않는다(VACUUM INTO는 기존 파일이 있으면 실패한다).
        again = db_backup.run_all(self.db, self.base)
        self.assertTrue(again["database"]["success"], again["database"])

    # --- D9: 읽기 전용 --------------------------------------------------------
    def test_readonly_connection_refuses_writes(self):
        _seed(self.db)
        svc = LedgerService(db_path=self.db, json_path=self.base / "d.json", base_dir=self.base)
        conn = svc.get_conn(readonly=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO documents (doc_id) VALUES ('X')")
        finally:
            conn.close()


def _extract_js_function(src: str, name: str) -> str:
    """조립된 대시보드 HTML에서 최상위 `function 이름(...) {...}` 하나를 잘라 낸다.

    `tests/test_audit_phase10_dashboard.py`의 `extract_js`와 같은 괄호 균형 방식이다.
    인라인 사본 대신 실물을 실행해야 소스·테스트 어긋남을 막을 수 있다. (감사 R6-02)
    """
    m = re.search(r"function " + name + r"\(", src)
    if not m:
        raise AssertionError(f"대시보드 템플릿에서 {name} 선언을 찾지 못했습니다")
    j = src.index("{", m.start())
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():k + 1]
    raise AssertionError(f"{name} 선언의 끝을 찾지 못했습니다")


class SearchLogRetentionTests(unittest.TestCase):
    """검색 로그 rolling 정리. (감사 R6-04)

    `search_log`는 검색할 때마다 1행씩 쌓이고 지우는 코드가 없어 영원히 늘었다.
    """
    def setUp(self):
        import datetime
        self.tmp = tempfile.mkdtemp(prefix="search_log_retention_")
        self.base = Path(self.tmp)
        self.db = self.base / "t.db"
        conn = sqlite3.connect(str(self.db))
        try:
            DatabaseManager.ensure_schema(conn)
            old = "2001-01-01T00:00:00"
            now = datetime.datetime.now().isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO search_log (query, mode, result_count, elapsed_ms, "
                "used_fts, searched_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("오래된 조회", "docs", 5, 10, 1, old),
                    ("오래된 0건", "docs", 0, 10, 0, old),
                    ("최근 조회", "docs", 3, 10, 1, now),
                ])
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _queries(self):
        conn = sqlite3.connect(str(self.db))
        try:
            return {r[0] for r in conn.execute("SELECT query FROM search_log")}
        finally:
            conn.close()

    def test_prune_deletes_only_expired_nonzero_rows(self):
        conn = sqlite3.connect(str(self.db))
        try:
            deleted = DatabaseManager.prune_search_log(conn)
        finally:
            conn.close()
        self.assertEqual(deleted, 1)
        self.assertEqual(self._queries(), {"오래된 0건", "최근 조회"})

    def test_keep_zero_result_can_be_disabled(self):
        conn = sqlite3.connect(str(self.db))
        try:
            deleted = DatabaseManager.prune_search_log(conn, keep_zero_result=False)
        finally:
            conn.close()
        self.assertEqual(deleted, 2)
        self.assertEqual(self._queries(), {"최근 조회"})

    def test_auto_prune_runs_at_most_once_per_day(self):
        from unittest import mock
        svc = LedgerService(db_path=self.db, json_path=self.base / "d.json",
                            base_dir=self.base)
        with mock.patch.object(DatabaseManager, "prune_search_log",
                                return_value=0) as pruned:
            svc._maybe_prune_search_log()
            svc._maybe_prune_search_log()
            self.assertEqual(pruned.call_count, 1)


class DashboardPayloadTests(unittest.TestCase):
    """대시보드 HTML 경량화 — 전문을 자르지 않고 중복만 없앤다."""

    def _payload(self):
        body = "# 문서\n\n앞부분입니다.\n\n" + "답변 본문 " * 50 + "\n\n뒷부분."
        answer = "답변 본문 " * 50
        # BMP 밖 문자(📌)가 답변 앞에 있으면 구 복원(substr)은 밀려 깨진다. (감사 R6-02)
        astral_answer = "이모지 뒤 답변"
        astral_body = "머리말 📌 기호와 " + astral_answer + " 뒷부분."
        return {
            "documents": [{"doc_id": "D1", "title": "제목", "full_markdown": body,
                           "answer_full_text": body[:20]},
                          {"doc_id": "D2", "title": "이모지 문서",
                           "full_markdown": astral_body, "answer_full_text": ""}],
            "qa_items": [
                {"qa_id": "D1-Q1", "doc_id": "D1", "q_num": 1, "question_title": "질문",
                 "answer_markdown": answer, "answer_full": answer[:20], "has_tables": "N"},
                # 부모 본문에 없는 답변은 예전처럼 전문을 그대로 싣는다.
                {"qa_id": "D1-Q2", "doc_id": "D1", "q_num": 2, "question_title": "질문2",
                 "answer_markdown": "부모 본문에 없는 답변", "answer_full": "", "has_tables": "N"},
                {"qa_id": "D2-Q1", "doc_id": "D2", "q_num": 1, "question_title": "이모지 질문",
                 "answer_markdown": astral_answer, "answer_full": "", "has_tables": "N"},
            ],
            "request_ledger": [],
        }

    def test_contained_answers_become_references(self):
        slim = DashboardRenderer.build_slim_payload(self._payload())
        by_id = {q["qa_id"]: q for q in slim["qa_items"]}
        self.assertIn("am_ref", by_id["D1-Q1"])
        self.assertNotIn("answer_markdown", by_id["D1-Q1"])
        self.assertIn("answer_markdown", by_id["D1-Q2"], "못 찾으면 전문을 그대로 실어야 한다")

    def test_rehydration_is_byte_identical(self):
        raw = self._payload()
        slim = DashboardRenderer.build_slim_payload(raw)
        restored = DashboardRenderer.rehydrate_qa_bodies(slim)
        original = {q["qa_id"]: q["answer_markdown"] for q in raw["qa_items"]}
        for q in restored["qa_items"]:
            self.assertEqual(q["answer_markdown"], original[q["qa_id"]],
                             f"{q['qa_id']}: 복원본이 원문과 다르다")

    def test_payload_is_smaller_than_inlining_everything(self):
        raw = self._payload()
        slim = DashboardRenderer.build_slim_payload(raw)
        inlined = DashboardRenderer.rehydrate_qa_bodies(slim)
        self.assertLess(len(json.dumps(slim, ensure_ascii=False)),
                        len(json.dumps(inlined, ensure_ascii=False)))

    def test_full_markdown_is_never_dropped(self):
        """전문 보존 정책 2항 — 문서 본문은 어떤 경우에도 빠지거나 잘리지 않는다."""
        raw = self._payload()
        slim = DashboardRenderer.build_slim_payload(raw)
        self.assertEqual(slim["documents"][0]["full_markdown"],
                         raw["documents"][0]["full_markdown"])

    def test_dashboard_js_rehydrates_same_as_python(self):
        """브라우저 쪽 복원 규칙이 파이썬과 같은 결과를 내는지 Node로 실행해 확인한다.

        인라인 사본이 아니라 조립 캐시(`dashboard_template.html`)의
        `rehydrateAnswerMarkdown` 실물을 떼어 실행한다. BMP 밖 문자 픽스처(D2-Q1) 포함.
        (감사 R6-02)
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("node가 없어 대시보드 JS를 실행할 수 없습니다")
        template = (SCRIPTS_DIR / "templates" / "dashboard_template.html").read_text(
            encoding="utf-8")
        # 복원 규칙은 문서당 한 번 본문을 분석하는 makeAnswerRehydrator에 있다(7회차 대용량 대응).
        func = (_extract_js_function(template, "makeAnswerRehydrator") + chr(10)
                + _extract_js_function(template, "rehydrateAnswerMarkdown"))
        raw = self._payload()
        slim = DashboardRenderer.build_slim_payload(raw)
        script = func + chr(10) + chr(10).join([
            "const DB = %s;" % json.dumps(slim, ensure_ascii=False),
            "const DOCS_MAP = {};",
            "DB.documents.forEach(d => { DOCS_MAP[d.doc_id] = d; });",
            "const out = {};",
            "DB.qa_items.forEach(q => {",
            "  const doc = DOCS_MAP[q.doc_id];",
            "  if (q.am_ref && !q.answer_markdown) {",
            "    const body = (doc && doc.full_markdown) || '';",
            "    q.answer_markdown = rehydrateAnswerMarkdown(body, q.am_ref);",
            "    delete q.am_ref;",
            "  }",
            "  out[q.qa_id] = q.answer_markdown;",
            "});",
            "console.log(JSON.stringify(out));",
        ])
        # stdin을 DEVNULL로 준다. 호출 셸의 stdin이 상속 불가능한 핸들이면
        # Windows에서 DuplicateHandle이 실패해 테스트가 환경 문제로 깨진다.
        proc = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        js_result = json.loads(proc.stdout)
        for q in raw["qa_items"]:
            self.assertEqual(js_result[q["qa_id"]], q["answer_markdown"],
                             f'{q["qa_id"]}: JS 복원이 원문과 다르다')


if __name__ == "__main__":
    unittest.main()
