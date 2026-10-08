# -*- coding: utf-8 -*-
"""DB 정합성 점검 (제안서 D2·D3).

예전에는 `PRAGMA integrity_check`가 `tools/purge_test_data.py`에만 있었고, FTS5
`integrity-check`는 어디에도 없었다. external-content FTS는 트리거로만 본체와 맞춰지므로
트리거를 우회한 쓰기·복원·중단된 마이그레이션이 있으면 인덱스가 조용히 어긋난다.
어긋나면 증상은 "검색 결과가 좀 이상하다"뿐이고, 아무도 원인을 못 찾는다.

불변조건 29("읽었는데 아무것도 없음을 성공으로 보고하지 않는다")와 같은 취지로,
이상은 숨기지 않고 `/api/sync`·`/api/db/health`와 00번 배치 끝에서 보이게 한다.
"""

from typing import Any
import sqlite3
from pathlib import Path

from . import fts_spec
from .migrations import SCHEMA_VERSION, get_version


def _scalar(cur, sql, params=()):
    row = cur.execute(sql, params).fetchone()
    return row[0] if row else None


def check_database(db_path, deep: bool = False, immutable: bool = False) -> dict:
    """DB 상태를 한 번에 점검한다.

    `deep=True`면 `PRAGMA integrity_check` 전체를 돈다(수백 MB에서 수 초). 기본은
    `quick_check`라 화면에서 눌러도 부담이 없다.

    `immutable=True`는 `-wal`/`-shm`을 전혀 건드리지 않지만 **아직 체크포인트되지 않은
    WAL 프레임을 보지 못한다.** 방금 고친 내용을 점검이 못 보고 "이상 없음"을 돌려주면
    점검을 믿을 수 없게 되므로 기본값은 False다. 운영 파일을 절대 건드리면 안 되는
    호출자(테스트 등)만 True를 준다.
    """
    db_path = Path(db_path)
    result = {
        "success": True,
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "problems": [],
        "warnings": [],
        # 고칠 수 없거나 고칠 필요가 없는 사실. 경고와 구분한다.
        "notes": [],
    }
    if not db_path.exists():
        result["success"] = False
        result["problems"].append("DB 파일이 없습니다")
        return result

    conn = None
    try:
        from extractors.ledger.workbook import connect_readonly
        conn = connect_readonly(db_path, immutable=immutable)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        result["size_bytes"] = db_path.stat().st_size
        result["schema_version"] = get_version(conn)
        result["expected_schema_version"] = SCHEMA_VERSION
        if result["schema_version"] < SCHEMA_VERSION:
            result["warnings"].append(
                f"스키마 버전이 낮습니다({result['schema_version']} < {SCHEMA_VERSION}). "
                f"다음 실행 때 자동으로 올라갑니다."
            )
        elif result["schema_version"] > SCHEMA_VERSION:
            result["problems"].append(
                f"DB 스키마 버전({result['schema_version']})이 프로그램({SCHEMA_VERSION})보다 높습니다. "
                f"프로그램을 업데이트하십시오."
            )

        # 1. 파일 무결성
        check_sql = "PRAGMA integrity_check" if deep else "PRAGMA quick_check"
        integrity = _scalar(cur, check_sql)
        result["integrity"] = integrity
        if integrity != "ok":
            result["problems"].append(f"파일 무결성 점검 실패: {integrity}")

        # 2. 외래키
        fk_rows = cur.execute("PRAGMA foreign_key_check").fetchall()
        result["foreign_key_violations"] = len(fk_rows)
        if fk_rows:
            result["problems"].append(f"외래키 위반 {len(fk_rows)}건")

        # 3. FTS 인덱스 무결성과 본체 대조
        fts_status = {}
        for fts_name, (source, _cols) in fts_spec.FTS_SPECS.items():
            entry: dict[str, Any] = {"ok": None, "rows": None, "source_rows": None}
            try:
                # `SELECT COUNT(*) FROM <fts>`는 쓸 수 없다. external-content FTS는 그 질의를
                # **본체 테이블**로 돌려보내기 때문에 언제나 본체와 같은 수가 나온다.
                # 인덱스가 실제로 몇 건을 담고 있는지는 그림자 테이블 `<fts>_docsize`가 안다.
                # 읽기 전용 연결이라 FTS5 'integrity-check' 명령(쓰기)은 여기서 못 쓴다.
                entry["rows"] = _scalar(cur, f"SELECT COUNT(*) FROM {fts_name}_docsize")
                entry["source_rows"] = _scalar(cur, f"SELECT COUNT(*) FROM {source}")
                entry["ok"] = entry["rows"] == entry["source_rows"]
                if not entry["ok"]:
                    result["problems"].append(
                        f"{fts_name} 인덱스 행수({entry['rows']})가 본체 {source}"
                        f"({entry['source_rows']})와 다릅니다. 재빌드가 필요합니다."
                    )
            except sqlite3.Error as e:
                entry["ok"] = False
                entry["error"] = str(e)
                result["warnings"].append(f"{fts_name} 점검 불가: {e}")
            fts_status[fts_name] = entry
        result["fts"] = fts_status

        # 4. 끊어진 연결 참조 (제안서 D3) — FK를 걸지 않고 계측만 한다.
        #    `linked_doc_id`에 FK를 걸면 문서 재빌드가 대장 링크를 지워, 불변조건 11
        #    (tombstone은 SQLite에만)과 충돌한다. 그래서 막지 않고 보이게만 한다.
        dangling_doc = _scalar(cur, """
            SELECT COUNT(*) FROM request_ledger r
            LEFT JOIN documents d ON r.linked_doc_id = d.doc_id
            WHERE COALESCE(r.linked_doc_id, '') != '' AND d.doc_id IS NULL
        """)
        dangling_ledger = _scalar(cur, """
            SELECT COUNT(*) FROM documents d
            LEFT JOIN request_ledger r ON d.linked_ledger_id = r.ledger_id
            WHERE COALESCE(d.linked_ledger_id, '') != '' AND r.ledger_id IS NULL
        """)
        orphan_qa = _scalar(cur, """
            SELECT COUNT(*) FROM qa_items q
            LEFT JOIN documents d ON q.doc_id = d.doc_id
            WHERE d.doc_id IS NULL
        """)
        result["links"] = {
            "dangling_linked_doc_id": dangling_doc,
            "dangling_linked_ledger_id": dangling_ledger,
            "orphan_qa_items": orphan_qa,
        }
        if dangling_doc:
            result["warnings"].append(f"없는 문서를 가리키는 대장 연결 {dangling_doc}건")
        if dangling_ledger:
            result["warnings"].append(f"없는 대장을 가리키는 문서 연결 {dangling_ledger}건")
        if orphan_qa:
            result["problems"].append(f"부모 문서가 없는 Q&A {orphan_qa}건")

        # 5. 값 품질 — 막지 않고 알린다. 운영 대장은 엑셀 자유 입력이 정본이라
        #    CHECK 제약을 걸면 동기화가 깨진다(불변조건 17·24).
        empty_year = _scalar(cur, "SELECT COUNT(*) FROM documents WHERE COALESCE(year, '') = ''")
        # 연도가 없는 문서 중 요구일자는 있는 것 — 이건 추출 결함이라 고칠 수 있다.
        fixable_year = _scalar(cur, """
            SELECT COUNT(*) FROM documents
            WHERE COALESCE(year, '') = '' AND COALESCE(request_date, '') != ''
        """)
        result["quality"] = {
            "documents_without_year": empty_year,
            "documents_without_year_but_dated": fixable_year,
            "status_values": {
                r["st"]: r["c"] for r in cur.execute(
                    "SELECT COALESCE(NULLIF(TRIM(status), ''), '(없음)') AS st, COUNT(*) AS c "
                    "FROM request_ledger GROUP BY st ORDER BY c DESC"
                )
            },
        }
        # 날짜가 아예 없는 문서는 고칠 수 없다. 연도 필터에서 빠지는 것이 맞는 동작이고,
        # 전체 검색에는 나온다. 사실로만 알리고 경고로 띄우지 않는다 — 고칠 수 없는 항목이
        # 매번 경고로 남으면 진짜 경고가 묻힌다.
        if fixable_year:
            result["warnings"].append(
                f"요구일자는 있는데 연도가 비어 연도 필터에서 빠지는 문서 {fixable_year}건 "
                f"(요구일자에서 채울 수 있습니다)"
            )
        if empty_year:
            result.setdefault("notes", []).append(
                f"원문에 날짜가 없어 연도를 알 수 없는 문서 {empty_year}건 — "
                f"전체 검색에는 나오고 연도 필터에서만 빠집니다"
            )

        result["counts"] = {
            t: _scalar(cur, f"SELECT COUNT(*) FROM {t}")
            for t in ("documents", "qa_items", "request_ledger", "ledger_history")
        }
    except sqlite3.Error as e:
        result["success"] = False
        result["problems"].append(f"점검 중 오류: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    result["success"] = not result["problems"]
    return result


def rebuild_fts_integrity(db_path) -> dict:
    """FTS 인덱스를 본체 기준으로 다시 만든다. 점검에서 어긋남이 나왔을 때 쓴다."""
    from .migrations import rebuild_fts
    conn = sqlite3.connect(str(db_path))
    try:
        ok = rebuild_fts(conn)
    finally:
        conn.close()
    return {"success": ok}
