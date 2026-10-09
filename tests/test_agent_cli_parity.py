# -*- coding: utf-8 -*-
"""CLI ↔ 기존 서비스 parity + 조회 무변경 + 비정상 입력 안정성.

- 동일 합성 DB에서 `LedgerService.search_all`과 CLI 검색의 ID 집합이 같다.
- 조회 전후 파일 지문이 변하지 않는다 (웜업 1회 후 정상 상태 기준).
- 손상 DB·빈 DB·특수문자 경로에서 stdout JSON 외 출력·트레이스백이 없다.
"""

import hashlib
import io
import json
import sqlite3
from contextlib import redirect_stdout

import agent_fixtures
from agent_bridge.facade import AgentFacade


def _run_cli(arg_list, base_dir):
    from agent_bridge.cli import main
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(["--data-root", str(base_dir)] + list(arg_list))
    out = buffer.getvalue()
    return json.loads(out), code, out


def _stable_ids(items):
    ids = set()
    for item in items:
        source = item.get("source_id", "")
        ids.add(source.split(":", 1)[1] if ":" in source else source)
    return ids


def test_search_parity_with_ledger_service(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    from services.ledger_service import LedgerService
    service = LedgerService(db_path=tmp_path / "data_requests.db",
                            json_path=tmp_path / "data_requests.json",
                            base_dir=tmp_path)
    with AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path]
                     )._no_search_logging(service):
        expected = service.search_all(kw="딥페이크", slim=True)
    expected_ids = ({r["ledger_id"] for r in expected["ledger"]}
                    | {r["doc_id"] for r in expected["documents"]}
                    | {r["qa_id"] for r in expected["qa_items"]})
    envelope, code, _ = _run_cli(["search", "딥페이크", "--limit", "50"], tmp_path)
    assert code == 0
    assert _stable_ids(envelope["data"]["items"]) == expected_ids


def _fingerprints(base_dir):
    marks = {}
    for name in ("data_requests.db", "data_requests.db-wal",
                 "data_requests.db-shm", "data_requests.json"):
        path = base_dir / name
        if path.exists():
            digest = hashlib.sha256()
            with open(str(path), "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            marks[name] = digest.hexdigest()
    return marks


def test_readonly_calls_leave_files_untouched(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    # 첫 호출로 파일 상태를 안정화한 뒤 비교한다 (정상 상태의 읽기는 무변경).
    _run_cli(["search", "딥페이크"], tmp_path)
    before = _fingerprints(tmp_path)
    _run_cli(["search", "딥페이크", "--category", "qa"], tmp_path)
    _run_cli(["document", "get", "DOC-2026-001"], tmp_path)
    _run_cli(["qa", "get", "QA-2026-001"], tmp_path)
    _run_cli(["ledger", "list"], tmp_path)
    _run_cli(["ledger", "show", "REQ-2026-001"], tmp_path)
    _run_cli(["ledger", "summary"], tmp_path)
    _run_cli(["ledger", "history", "REQ-2026-001"], tmp_path)
    _run_cli(["reconcile"], tmp_path)
    _run_cli(["doctor"], tmp_path)
    assert _fingerprints(tmp_path) == before


def test_search_log_not_written(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    _run_cli(["search", "딥페이크"], tmp_path)
    conn = sqlite3.connect(str(tmp_path / "data_requests.db"))
    try:
        count = conn.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_missing_and_corrupt_db_fail_cleanly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    envelope, code, out = _run_cli(["search", "딥페이크"], empty)
    assert code == 3 and envelope["ok"] is False
    assert envelope["error"]["code"] == "dataset_unavailable"
    assert json.loads(out)["ok"] is False

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "data_requests.db").write_bytes(b"not a database at all")
    envelope, code, out = _run_cli(["search", "딥페이크"], corrupt)
    assert code in (1, 3) and envelope["ok"] is False
    assert json.loads(out)["ok"] is False  # stdout은 JSON만, 트레이스백 없음


def test_special_char_path_works(tmp_path):
    target = tmp_path / "자료 요청 (2026) [최종]"
    target.mkdir()
    agent_fixtures.build_synthetic_db(target)
    envelope, code, _ = _run_cli(["search", "딥페이크"], target)
    assert code == 0 and envelope["ok"] is True
