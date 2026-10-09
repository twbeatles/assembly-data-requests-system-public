# -*- coding: utf-8 -*-
"""보안 계약 테스트: 적대적 문서·경로 탈출·대량 반출·원시 실행 수단 없음."""

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout

import agent_fixtures
from agent_bridge import policy
from agent_bridge.errors import DataReqError
from agent_bridge.facade import AgentFacade


def _facade(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    return AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])


def test_hostile_document_is_data_only(tmp_path):
    facade = _facade(tmp_path)
    hostile = agent_fixtures.add_hostile_doc(tmp_path)
    found = facade.search("지시문", category="doc")
    ids = [item["source_id"] for item in found["items"]]
    assert "doc:%s" % hostile in ids
    # 도구 실행으로 연결되지 않음: 대장·DB 행 수 불변, 삭제 없음
    conn = sqlite3.connect(str(tmp_path / "data_requests.db"))
    try:
        ledgers = conn.execute(
            "SELECT COUNT(*) FROM request_ledger").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert ledgers == 5
    assert {"documents", "qa_items", "request_ledger"} <= tables


def test_no_raw_execution_surface(tmp_path):
    import agent_bridge.facade as facade_mod
    import agent_bridge.mcp_server as mcp_mod
    import inspect
    facade_names = [name for name, _ in inspect.getmembers(facade_mod.AgentFacade,
                                                           predicate=inspect.isfunction)]
    for name in facade_names:
        lowered = name.lower()
        assert "sql" not in lowered and "shell" not in lowered and "exec" not in lowered, name
    assert not hasattr(mcp_mod, "datareq_execute_sql")
    assert not hasattr(mcp_mod, "datareq_run_shell")


def test_path_traversal_denied(tmp_path):
    facade = _facade(tmp_path)
    for evil in ["..", "../..", "C:/Windows/System32", "/etc/passwd",
                 str(tmp_path / ".." / "outside")]:
        try:
            facade.pipeline_plan(evil)
        except DataReqError as exc:
            assert exc.code == "policy_denied"
        else:
            raise AssertionError("must deny: %s" % evil)


def test_bulk_export_is_capped(tmp_path):
    facade = _facade(tmp_path)
    found = facade.search("", category="all", limit=10 ** 6)
    assert len(found["items"]) <= policy.MAX_LIMIT
    doc = facade.get_document("DOC-2024-001", max_chars=10 ** 9)
    assert len(doc["excerpt"]) <= policy.MAX_EXCERPT_CHARS
    pack = facade.evidence_pack("딥페이크", max_sources=10 ** 6)
    assert len(pack["sources"]) <= policy.MAX_SOURCES


def test_stdout_is_json_only_on_errors(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    from agent_bridge.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out):
        with redirect_stderr(err):
            code = main(["--data-root", str(tmp_path),
                         "ledger", "show", "REQ-NOPE"])
    assert code == 2
    assert json.loads(out.getvalue())["error"]["code"] == "invalid_input"
    assert "Traceback" not in out.getvalue()
