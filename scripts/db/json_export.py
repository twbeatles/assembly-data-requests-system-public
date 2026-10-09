# -*- coding: utf-8 -*-
"""SQLite → `data_requests.json` 스트리밍 내보내기.

JSON 캐시는 문서 전문을 담아 DB가 커지면 수백 MB가 된다. 예전에는 대장 한 건을 고칠 때마다
세 테이블을 통째로 파이썬 리스트로 읽고(`fetchall`) `indent=2`로 다시 썼다. 메모리를 DB 크기만큼
쓰고 파일도 들여쓰기만큼 부풀었다. 여기서는 행을 하나씩 읽어 한 줄씩 쓴다.

- 메모리: 가장 큰 행 하나 수준.
- 형식: 최상위 키와 행 순서는 예전과 같다. 행마다 한 줄(구분자 공백 없음)이라
  `json.load`로 그대로 읽힌다.
- 교체: 임시 파일에 다 쓴 뒤 원자 교체한다. 실패하면 기존 파일이 그대로 남는다.
"""
import json
import os
import shutil
import time
from pathlib import Path

EXPORT_QUERIES = (
    ("documents", "SELECT * FROM documents ORDER BY year DESC, request_date DESC, doc_id ASC"),
    ("qa_items", "SELECT * FROM qa_items ORDER BY year DESC, request_date DESC, qa_id ASC"),
    # 삭제 tombstone은 파생 산출물에 넣지 않는다(불변조건 11).
    ("request_ledger", "SELECT * FROM request_ledger WHERE COALESCE(status, '') != '[삭제]' "
                       "ORDER BY year DESC, request_date DESC, seq_no DESC"),
)

_SEPARATORS = (",", ":")


def _write_rows(fp, cursor) -> int:
    count = 0
    names = [d[0] for d in cursor.description]
    for row in cursor:
        fp.write(",\n" if count else "\n")
        fp.write(json.dumps(dict(zip(names, row)), ensure_ascii=False, separators=_SEPARATORS))
        count += 1
    return count


def replace_with_retry(tmp_path: Path, path: Path, attempts: int = 3):
    """Windows에서 다른 프로세스가 잠깐 열고 있어도 교체되게 짧게 재시도한다."""
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            time.sleep(0.1 * attempt)
    shutil.copy2(tmp_path, path)
    Path(tmp_path).unlink(missing_ok=True)


def export_combined_json(conn, path) -> dict:
    """DB 연결에서 문서·Q&A·대장을 읽어 JSON 캐시를 원자적으로 다시 쓴다. 반환: 테이블별 행 수."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp")
    counts = {}
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
            fp.write("{")
            for idx, (key, sql) in enumerate(EXPORT_QUERIES):
                fp.write(("," if idx else "") + f"\n{json.dumps(key)}:[")
                counts[key] = _write_rows(fp, conn.execute(sql))
                fp.write("\n]")
            fp.write("\n}\n")
        replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return counts


def write_json_compact(path, data: dict):
    """메모리에 이미 있는 결합 데이터를 같은 형식(행마다 한 줄)으로 원자 저장한다."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
            fp.write("{")
            for idx, (key, value) in enumerate(data.items()):
                fp.write(("," if idx else "") + f"\n{json.dumps(key)}:")
                if isinstance(value, list):
                    fp.write("[")
                    for i, row in enumerate(value):
                        fp.write(",\n" if i else "\n")
                        fp.write(json.dumps(row, ensure_ascii=False, separators=_SEPARATORS))
                    fp.write("\n]")
                else:
                    fp.write(json.dumps(value, ensure_ascii=False, separators=_SEPARATORS))
            fp.write("\n}\n")
        replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
