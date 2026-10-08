# -*- coding: utf-8 -*-
"""FTS5 인덱스 정의의 단일 진실 원천(SSOT).

예전에는 같은 DDL이 `ensure_schema`(IF NOT EXISTS)와 `build_fts_and_indexes`(새로 생성)에
두 벌로 적혀 있었다. 한쪽만 고치면 신규 설치와 재빌드의 인덱스 구성이 갈라진다.
이 표 하나에서 CREATE·트리거·재구축 SQL을 모두 만든다.

인덱싱 컬럼은 **정본**이어야 한다. `answer_full_text`(엑셀 3만 자 잘림본)와
`answer_full`은 각각 `full_markdown`·`answer_markdown`의 잘린 사본이라 인덱싱하지 않는다.
전에는 잘림본을 색인해 서버·GUI 검색이 본문 뒷부분을 통째로 놓쳤다(제안서 S1).
"""

# 테이블명 -> (본체 테이블, 인덱싱 컬럼). 컬럼 순서가 bm25 가중치 순서다.
FTS_SPECS = {
    "documents_fts": (
        "documents",
        ("title", "requester", "doc_number", "question_list", "full_markdown"),
    ),
    "qa_items_fts": (
        "qa_items",
        ("question_title", "requester", "answer_markdown"),
    ),
    "request_ledger_fts": (
        "request_ledger",
        ("title", "details", "requester", "party", "seq_no", "note"),
    ),
}

# 파이썬 fallback 평가가 쓰는 비교 필드. FTS 컬럼과 같아야 두 경로의 결과가 일치한다(제안서 S3).
# 초성 비교는 짧은 필드만 쓴다. 본문 전체를 초성으로 바꾸면 검색 한 번에 수십 MB를 훑는다.
FTS_CHOSEONG_FIELDS = {
    "documents_fts": ("title", "requester"),
    "qa_items_fts": ("question_title", "requester"),
    "request_ledger_fts": ("title", "requester", "party"),
}

# bm25 가중치. 제목이 가장 무겁고 본문이 가장 가볍다. 번호 컬럼은 부분문자열 소음이 많아 낮춘다.
FTS_WEIGHTS = {
    "documents_fts": (5.0, 3.0, 0.5, 2.0, 1.0),
    "qa_items_fts": (5.0, 3.0, 1.5),
    "request_ledger_fts": (5.0, 2.0, 3.0, 1.0, 0.5, 1.0),
}

FTS_TRIGGER_SUFFIXES = ("ai", "ad", "au")


def fts_table_names():
    return tuple(FTS_SPECS.keys())


def fts_trigger_names():
    """모든 FTS 동기화 트리거 이름. 드롭 순서에 쓴다."""
    names = []
    for fts_name, (source, _cols) in FTS_SPECS.items():
        for suffix in FTS_TRIGGER_SUFFIXES:
            names.append(f"{source}_{suffix}")
    return tuple(names)


def weights_sql(fts_name: str) -> str:
    """bm25() 인자로 넣을 가중치 문자열."""
    return ", ".join(str(w) for w in FTS_WEIGHTS[fts_name])


def create_sql(fts_name: str, if_not_exists: bool = False) -> str:
    source, cols = FTS_SPECS[fts_name]
    guard = "IF NOT EXISTS " if if_not_exists else ""
    col_sql = ",\n                    ".join(cols)
    return f"""
        CREATE VIRTUAL TABLE {guard}{fts_name} USING fts5(
                    {col_sql},
                    content='{source}',
                    content_rowid='rowid',
                    tokenize='trigram'
        )
    """


def populate_sql(fts_name: str) -> str:
    """본체 테이블의 현재 내용을 인덱스에 채운다."""
    source, cols = FTS_SPECS[fts_name]
    col_list = ", ".join(cols)
    return (
        f"INSERT INTO {fts_name}(rowid, {col_list}) "
        f"SELECT rowid, {col_list} FROM {source}"
    )


def trigger_sqls(fts_name: str, if_not_exists: bool = False) -> list:
    """INSERT/DELETE/UPDATE 동기화 트리거 세 개.

    external-content FTS는 본체와 자동으로 맞춰지지 않는다. 트리거가 빠지면 인덱스가
    조용히 어긋나고, 증상은 '검색 결과가 좀 이상하다'뿐이다(제안서 D2).
    """
    source, cols = FTS_SPECS[fts_name]
    guard = "IF NOT EXISTS " if if_not_exists else ""
    col_list = ", ".join(cols)
    new_vals = ", ".join(f"new.{c}" for c in cols)
    old_vals = ", ".join(f"old.{c}" for c in cols)
    insert_new = (
        f"INSERT INTO {fts_name}(rowid, {col_list}) VALUES (new.rowid, {new_vals});"
    )
    delete_old = (
        f"INSERT INTO {fts_name}({fts_name}, rowid, {col_list}) "
        f"VALUES ('delete', old.rowid, {old_vals});"
    )
    return [
        f"CREATE TRIGGER {guard}{source}_ai AFTER INSERT ON {source} BEGIN\n    {insert_new}\nEND;",
        f"CREATE TRIGGER {guard}{source}_ad AFTER DELETE ON {source} BEGIN\n    {delete_old}\nEND;",
        f"CREATE TRIGGER {guard}{source}_au AFTER UPDATE ON {source} BEGIN\n"
        f"    {delete_old}\n    {insert_new}\nEND;",
    ]


def drop_sqls():
    """FTS 테이블과 트리거를 지우는 SQL. 트리거를 먼저 지운다."""
    stmts = [f"DROP TRIGGER IF EXISTS {name}" for name in fts_trigger_names()]
    stmts += [f"DROP TABLE IF EXISTS {name}" for name in fts_table_names()]
    return stmts


def integrity_sql(fts_name: str) -> str:
    return f"INSERT INTO {fts_name}({fts_name}) VALUES('integrity-check')"
