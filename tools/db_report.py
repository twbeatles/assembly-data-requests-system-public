# -*- coding: utf-8 -*-
"""DB 용량·건강 진단 (제안서 D2·D8).

이 환경의 SQLite에는 `dbstat` 가상 테이블이 없어 "어느 테이블이 몇 MB를 쓰는가"를
물어볼 수 없다. 컬럼 길이 합과 `page_count × page_size`로 근사한다.

사용법:
    python tools/db_report.py                     # 기본 DB 진단
    python tools/db_report.py --db 경로            # 다른 DB
    python tools/db_report.py --deep              # integrity_check 전체
    python tools/db_report.py --rebuild-fts       # 인덱스 어긋남 복구
"""

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from db import fts_spec  # noqa: E402
from db.health import check_database, rebuild_fts_integrity  # noqa: E402

# 용량을 많이 쓰는 텍스트 컬럼. 근사치 계산용이다.
TEXT_COLUMNS = {
    "documents": ("full_markdown", "answer_full_text", "answer_summary",
                  "question_list", "table_summary", "title"),
    "qa_items": ("answer_markdown", "answer_full", "question_title"),
    "request_ledger": ("details", "note", "title"),
}


def _mb(n):
    return f"{(n or 0) / 1048576:8.2f} MB"


def storage_report(db_path: Path):
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        page_count = cur.execute("PRAGMA page_count").fetchone()[0]
        page_size = cur.execute("PRAGMA page_size").fetchone()[0]
        freelist = cur.execute("PRAGMA freelist_count").fetchone()[0]
        print(f"파일 크기(페이지 기준) {_mb(page_count * page_size)}"
              f"   빈 페이지 {freelist} ({_mb(freelist * page_size)})")
        print()
        print("텍스트 컬럼 용량(문자 길이 합 — UTF-8 실제 바이트는 한글에서 약 3배)")
        for table, columns in TEXT_COLUMNS.items():
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
            except sqlite3.Error:
                continue
            rows = cur.fetchone()[0]
            print(f"  [{table}] {rows}행")
            for col in columns:
                try:
                    total = cur.execute(f"SELECT SUM(LENGTH({col})) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    continue
                print(f"      {col:20} {_mb(total)}")
        print()
        print("FTS 인덱스")
        for fts_name, (source, cols) in fts_spec.FTS_SPECS.items():
            try:
                n = cur.execute(f"SELECT COUNT(*) FROM {fts_name}").fetchone()[0]
                data = cur.execute(
                    f"SELECT SUM(LENGTH(block)) FROM {fts_name}_data"
                ).fetchone()[0]
            except sqlite3.Error as e:
                print(f"  {fts_name}: 읽을 수 없음 ({e})")
                continue
            print(f"  {fts_name:22} {n:6}행  인덱스 {_mb(data)}  컬럼: {', '.join(cols)}")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="SQLite 용량·건강 진단")
    ap.add_argument("--db", default=str(REPO_ROOT / "data_requests.db"))
    ap.add_argument("--deep", action="store_true", help="integrity_check 전체 실행")
    ap.add_argument("--rebuild-fts", action="store_true", help="FTS 인덱스를 본체 기준으로 재구축")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB가 없습니다: {db_path}")
        return 1

    print(f"=== {db_path} ===")
    storage_report(db_path)
    print()
    print("=== 정합성 점검 ===")
    report = check_database(db_path, deep=args.deep)
    print(f"스키마 버전 {report.get('schema_version')} (기대 {report.get('expected_schema_version')})")
    print(f"무결성 {report.get('integrity')}   외래키 위반 {report.get('foreign_key_violations')}")
    for name, entry in (report.get("fts") or {}).items():
        mark = "OK" if entry.get("ok") else "불일치"
        print(f"  {name:22} {mark}  인덱스 {entry.get('rows')}행 / 본체 {entry.get('source_rows')}행")
    print(f"연결 참조: {report.get('links')}")
    print(f"건수: {report.get('counts')}")
    for n in report.get("notes", []):
        print(f"  [참고] {n}")
    for w in report.get("warnings", []):
        print(f"  [경고] {w}")
    for pr in report.get("problems", []):
        print(f"  [문제] {pr}")

    if args.rebuild_fts:
        print()
        print("FTS 인덱스를 다시 만듭니다...")
        print(rebuild_fts_integrity(db_path))

    return 0 if report.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
