# -*- coding: utf-8 -*-
"""Golden 검색 평가용 합성 코퍼스. 운영 데이터에 절대 쓰지 않는다.

계획서 §8.1 그룹을 합성 문서로 커버한다: 완성형 2자/3자, 초성, 띄어쓰기,
조사, 동의어, OR·제외어·구문, 연도·필드 한정, 제목 전용·본문 뒷부분 전용,
Q&A-부모 중복, 표·숫자 답변, tombstone, 중복 제목, 무결과 케이스.
"""

import sqlite3
from pathlib import Path

LONG_FILLER = "설명 문장이다. " * 60


def build_golden_db(base_dir):
    """Golden DB를 만들고 쿼리 기대값과 함께 돌려준다."""
    from db.database_manager import DatabaseManager

    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / "data_requests.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        DatabaseManager.ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()

    docs = [
        ("G-TITLE-ONLY", "2026", "2026-01-05", "국회", "김철수", "1",
         "고유어갑 관련 답변", "질의", "이 본문에는 표제어가 없다. " + LONG_FILLER),
        ("G-TAIL-ONLY", "2026", "2026-02-05", "국회", "이영희", "2",
         "본문 뒷부분 문서", "질의", LONG_FILLER + "맨 끝 고유어 토착왜가리"),
        ("G-QA-PARENT", "2025", "2025-03-05", "대외기관", "박민수", "3",
         "질의중복어 안내", "질의", "질의중복어에 대한 종합 설명이다. " + LONG_FILLER),
        ("G-TABLE", "2025", "2025-04-05", "대외기관", "최동훈", "4",
         "통계표고유어 현황", "질의",
         "통계표고유어 2024년 120건, 2025년 135건이다. 단위 건."),
        ("G-SYN", "2026", "2026-05-05", "대외기관", "김철수", "5",
         "심의위 안내", "질의", "대외위원회 신속심의 절차 안내다. " + LONG_FILLER),
        ("G-SPACING", "2026", "2026-06-05", "국회", "이영희", "6",
         "범죄 대응", "질의", "디지털성범죄 대응 절차다. " + LONG_FILLER),
        ("G-PARTICLE", "2024", "2024-07-05", "국회", "박민수", "7",
         "메신저 대응", "질의", "텔레그램 유포 차단 절차다. " + LONG_FILLER),
        ("G-CHOSUNG", "2024", "2024-08-05", "국회", "최동훈", "8",
         "텔레그램 안내", "질의", "메신저 관련 일반 안내다. " + LONG_FILLER),
        ("G-TWOCHAR", "2026", "2026-09-05", "국회", "김철수", "9",
         "일반 안내", "질의", "국회 요구자료 처리 일반 절차다. " + LONG_FILLER),
        ("G-YEAR-2025", "2025", "2025-10-05", "국회", "이영희", "10",
         "연도고유어 2025", "질의", "연도고유어 2025년 기록이다."),
        ("G-YEAR-2026", "2026", "2026-10-05", "국회", "이영희", "11",
         "연도고유어 2026", "질의", "연도고유어 2026년 기록이다."),
        ("G-DUP-2024", "2024", "2024-11-05", "국회", "박민수", "12",
         "중복제목문서", "질의", "중복제목문서 2024년판이다."),
        ("G-DUP-2026", "2026", "2026-11-05", "국회", "박민수", "13",
         "중복제목문서", "질의", "중복제목문서 2026년판이다."),
        ("G-OR-TW", "2026", "2026-12-05", "국회", "최동훈", "14",
         "트위터 안내", "질의", "트위터 유포 대응 절차다."),
        ("G-EX-ONLY", "2026", "2026-01-15", "국회", "김철수", "15",
         "딥페이크 단독", "질의", "딥페이크 대응 단독 문서다."),
        ("G-EX-BOTH", "2026", "2026-02-15", "국회", "김철수", "16",
         "딥페이크 복합", "질의", "딥페이크와 텔레그램 복합 문서다."),
        ("G-PHRASE", "2025", "2025-05-15", "대외기관", "이영희", "17",
         "구문 문서", "질의", "신속심의 제도로 처리한다."),
        ("G-LIVE", "2026", "2026-03-15", "국회", "박민수", "18",
         "삭제고유어 포함 답변", "질의", "삭제고유어가 본문에 있다."),
    ]
    qas = [
        ("G-QA-1", "G-QA-PARENT", "2025", "2025-03-05", "대외기관",
         "박민수", 1, "질의중복어란?", "질의중복어 답변이다. " + LONG_FILLER),
    ]
    ledgers = [
        ("REQ-G-001", "2026", "1", "무소속", "김철수", "", "고유어갑 대장 건",
         "대장 세부", "2026-01-01", "", "", "전담팀", "미제출", "", "시스템", ""),
        ("REQ-G-TOMB", "2026", "2", "무소속", "삭제됨", "", "삭제고유어 대장 건",
         "대장 세부", "2026-01-02", "", "", "전담팀", "[삭제]", "", "시스템", ""),
    ]
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for doc_id, year, date, inst, requester, number, title, qlist, body in docs:
            cur.execute(
                "INSERT INTO documents (doc_id, year, request_date, institution, "
                "requester, doc_number, title, question_list, full_markdown) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (doc_id, year, date, inst, requester, number, title, qlist, body))
        for qa_id, doc_id, year, date, inst, requester, qnum, qtitle, ans in qas:
            cur.execute(
                "INSERT INTO qa_items (qa_id, doc_id, year, request_date, "
                "institution, requester, q_num, question_title, answer_markdown) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (qa_id, doc_id, year, date, inst, requester, qnum, qtitle, ans))
        for row in ledgers:
            cur.execute(
                "INSERT INTO request_ledger (ledger_id, year, seq_no, party, "
                "requester, aide, title, details, request_date, deadline, "
                "submit_date, department, status, note, request_type, "
                "linked_doc_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row + ("2026-01-01 00:00:00.000000", "2026-01-02 00:00:00.000000"))
        conn.commit()
    finally:
        conn.close()
    return {"doc_ids": [d[0] for d in docs]}


# query / category / year / 반드시 포함 / 반드시 제외
GOLDEN_QUERIES = [
    {"name": "title_only", "query": "고유어갑", "category": "all", "year": None,
     "must_include": {"doc:G-TITLE-ONLY"}, "must_exclude": set()},
    {"name": "body_tail", "query": "토착왜가리", "category": "all", "year": None,
     "must_include": {"doc:G-TAIL-ONLY"}, "must_exclude": set()},
    {"name": "synonym", "query": "심의위", "category": "all", "year": None,
     "must_include": {"doc:G-SYN"}, "must_exclude": set()},
    {"name": "spacing", "query": "디지털 성범죄", "category": "all", "year": None,
     "must_include": {"doc:G-SPACING"}, "must_exclude": set()},
    {"name": "particle", "query": "텔레그램을", "category": "all", "year": None,
     "must_include": {"doc:G-PARTICLE"}, "must_exclude": set()},
    {"name": "chosung", "query": "ㅌㄹㄱㄹ", "category": "all", "year": None,
     "must_include": {"doc:G-CHOSUNG"}, "must_exclude": set()},
    {"name": "two_char", "query": "국회", "category": "doc", "year": None,
     "must_include": {"doc:G-TWOCHAR"}, "must_exclude": set()},
    {"name": "or_query", "query": "텔레그램 OR 트위터", "category": "doc", "year": None,
     "must_include": {"doc:G-PARTICLE", "doc:G-OR-TW"}, "must_exclude": set()},
    {"name": "exclusion", "query": "딥페이크 -텔레그램", "category": "doc", "year": None,
     "must_include": {"doc:G-EX-ONLY"}, "must_exclude": {"doc:G-EX-BOTH"}},
    {"name": "phrase", "query": '"신속심의"', "category": "doc", "year": None,
     "must_include": {"doc:G-PHRASE"}, "must_exclude": set()},
    {"name": "year_filter", "query": "연도고유어", "category": "doc", "year": "2025",
     "must_include": {"doc:G-YEAR-2025"}, "must_exclude": {"doc:G-YEAR-2026"}},
    {"name": "requester_field", "query": "요구자:김철수 고유어갑", "category": "ledger",
     "year": None, "must_include": {"ledger:REQ-G-001"}, "must_exclude": set()},
    {"name": "tombstone_excluded", "query": "삭제고유어", "category": "ledger",
     "year": None, "must_include": set(), "must_exclude": {"ledger:REQ-G-TOMB"}},
    {"name": "qa_overlap", "query": "질의중복어", "category": "all", "year": None,
     "must_include": {"doc:G-QA-PARENT", "qa:G-QA-1"}, "must_exclude": set()},
    {"name": "no_results", "query": "존재하지않는질의xyz", "category": "all",
     "year": None, "must_include": set(), "must_exclude": set()},
]
