# -*- coding: utf-8 -*-
"""스키마 버전 관리와 마이그레이션 (제안서 D1).

예전에는 `PRAGMA user_version`을 쓰지 않아 스키마를 바꾸는 유일한 수단이 전체 드롭 후
재빌드였다. 배포본이 부서마다 자기 DB를 들고 있으므로, **버전이 다르다는 사실 자체를
감지할 수단이 없는 것**이 가장 큰 위험이었다.

규칙
- 올리는 단계는 파괴적이지 않은 것만 자동 적용한다(ADD COLUMN, CREATE INDEX, 인덱스 재구축).
  컬럼 삭제·타입 변경은 여기서 하지 않고 파이프라인 재빌드로 돌린다.
- DB 버전이 코드 기대값보다 **높으면** 조용히 진행하지 않고 거절한다. 구 버전 프로그램이
  신 DB를 열어 새 컬럼을 지워 버리는 경로를 막는다.
- 마이그레이션은 호출자가 파이프라인 락을 쥔 상태에서 돈다(불변조건 28).
"""

from __future__ import annotations

import sqlite3

from . import fts_spec

SCHEMA_VERSION = 3


class SchemaTooNewError(RuntimeError):
    """DB가 이 프로그램보다 최신 스키마일 때. 프로그램을 업데이트해야 한다."""


def get_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_version(conn: sqlite3.Connection, version: int):
    # PRAGMA는 파라미터 바인딩을 받지 않는다. 정수로 강제해 주입 여지를 없앤다.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn, table: str) -> set:
    if not _table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _is_fresh_database(conn) -> bool:
    """아직 본체 테이블이 없으면 새 DB다. 마이그레이션 없이 현재 버전을 찍는다."""
    return not _table_exists(conn, "documents")


def _add_column(conn, table: str, column: str, decl: str) -> bool:
    if not _table_exists(conn, table) or column in _columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def _migrate_to_2(conn) -> list:
    """v1 -> v2.

    - 대장 이력에 `changed_fields`·`actor`를 더한다. 통짜 JSON 스냅숏만으로는 '어느 필드가
      바뀌었나'를 SQL로 물어볼 수 없었다(제안서 D7).
    - FTS 인덱스를 정본 컬럼으로 다시 만든다(제안서 S1·S3). 컬럼 구성이 바뀌므로
      가상 테이블을 다시 만드는 것 말고는 방법이 없다. 본체 데이터는 건드리지 않는다.
    """
    done = []
    if _add_column(conn, "ledger_history", "changed_fields", "TEXT"):
        done.append("ledger_history.changed_fields 추가")
    if _add_column(conn, "ledger_history", "actor", "TEXT"):
        done.append("ledger_history.actor 추가")
    if rebuild_fts(conn):
        done.append("FTS 인덱스를 정본 컬럼으로 재구축")
    return done


# 버전 -> (설명, 적용 함수). 키는 "이 단계를 적용하면 도달하는 버전"이다.
def _migrate_to_3(conn):
    from .ledger_outbox import ensure_tables
    ensure_tables(conn)
    return ["대장 엑셀 재시도·등록 멱등성 저장소"]


MIGRATIONS = {
    3: ("대장 변경의 내구성 보장", _migrate_to_3),
    2: ("FTS 정본 컬럼 전환 및 대장 이력 필드 확장", _migrate_to_2),
}


def rebuild_fts(conn: sqlite3.Connection) -> bool:
    """FTS 가상 테이블과 트리거를 정의대로 다시 만들고 본체 내용을 채운다.

    본체 테이블은 읽기만 한다. FTS5를 못 쓰는 환경에서는 조용히 False를 돌려준다.
    """
    cur = conn.cursor()
    try:
        for stmt in fts_spec.drop_sqls():
            cur.execute(stmt)
        for name in fts_spec.fts_table_names():
            cur.execute(fts_spec.create_sql(name))
            cur.execute(fts_spec.populate_sql(name))
            for trg in fts_spec.trigger_sqls(name):
                cur.execute(trg)
        conn.commit()
        return True
    except sqlite3.Error as e:
        conn.rollback()
        print(f"FTS 인덱스 재구축 실패(검색은 파이썬 대체 경로로 동작): {e}")
        return False


def apply_migrations(conn: sqlite3.Connection, verbose: bool = True,
                     fresh_hint: bool | None = None) -> dict:
    """필요한 마이그레이션을 순서대로 적용하고 결과를 돌려준다.

    `fresh_hint`: 호출자가 테이블을 만들기 **전에** 본 상태. `ensure_schema`는 테이블을
    먼저 만들고 이 함수를 부르므로, 여기서 `documents` 존재 여부만 보면 새 DB도 언제나
    '기존 DB'로 판정된다. 그러면 빌드할 때마다 빈 테이블에 마이그레이션을 돌리고
    "[DB 마이그레이션]"을 찍어, 진짜 마이그레이션이 일어난 순간이 소음에 묻힌다.

    반환: {"from": 이전버전, "to": 현재버전, "applied": [단계 설명...], "fresh": bool}
    """
    current = get_version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"이 DB의 스키마 버전({current})이 프로그램이 아는 버전({SCHEMA_VERSION})보다 높습니다. "
            f"프로그램을 최신 배포본으로 업데이트한 뒤 다시 실행하십시오."
        )

    is_fresh = _is_fresh_database(conn) if fresh_hint is None else bool(fresh_hint)
    if is_fresh:
        set_version(conn, SCHEMA_VERSION)
        conn.commit()
        return {"from": current, "to": SCHEMA_VERSION, "applied": [], "fresh": True}

    if current == SCHEMA_VERSION:
        return {"from": current, "to": current, "applied": [], "fresh": False}

    applied = []
    for target in sorted(MIGRATIONS):
        if target <= current:
            continue
        label, fn = MIGRATIONS[target]
        steps = fn(conn) or []
        set_version(conn, target)
        conn.commit()
        applied.extend(steps)
        if verbose:
            print(f"[DB 마이그레이션] v{target}: {label}")
            for s in steps:
                print(f"    - {s}")

    return {"from": current, "to": get_version(conn), "applied": applied, "fresh": False}
