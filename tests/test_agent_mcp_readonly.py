# -*- coding: utf-8 -*-
"""MCP 도구 목록·호출·읽기 전용 테스트. 합성 픽스처만 쓴다 (운영 데이터 금지)."""

import asyncio
import hashlib
import json
import os

import agent_fixtures

EXPECTED_TOOLS = {"datareq_status", "datareq_search", "datareq_get_document",
                  "datareq_get_qa", "datareq_ledger_list", "datareq_ledger_get",
                  "datareq_ledger_history", "datareq_evidence_pack"}

FORBIDDEN_FRAGMENTS = ("delete", "restore", "quarantine", "open_file",
                       "run_shell", "run_pipeline", "execute_sql",
                       "send_email", "export_all", "overwrite")


def _make_server(tmp_path):
    os.environ["DATAREQ_DATA_ROOT"] = str(tmp_path)
    os.environ["DATAREQ_PROFILE"] = "reader"
    from agent_bridge.mcp_server import create_server
    return create_server()


def _call(server, name, arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    assert texts, "tool must return text content"
    return json.loads(texts[0])


def test_tool_list_is_readonly_set(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    server = _make_server(tmp_path)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert EXPECTED_TOOLS <= names
    for name in names:
        lowered = name.lower()
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in lowered, name


def test_tools_return_json_and_search_works(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    server = _make_server(tmp_path)
    status = _call(server, "datareq_status", {})
    assert status["ok"] is True
    assert status["data"]["documents"] == 3
    found = _call(server, "datareq_search", {"query": "딥페이크"})
    assert found["ok"] is True
    assert found["data"]["items"], "synthetic match expected"
    doc = _call(server, "datareq_get_document",
                {"doc_id": "DOC-2026-001", "max_chars": 20})
    assert doc["ok"] is True
    assert doc["data"]["truncated"] is True
    assert len(doc["data"]["excerpt"]) <= 21
    assert doc["data"]["next_offset"] == 20
    assert doc["data"]["content_sha256"].startswith("sha256:")
    history = _call(server, "datareq_ledger_history",
                    {"ledger_id": "REQ-2026-001"})
    assert history["ok"] is True
    assert "010-1234-5678" not in json.dumps(history, ensure_ascii=False)


def _fingerprints(base_dir):
    from pathlib import Path
    marks = {}
    for name in ("data_requests.db", "data_requests.db-wal",
                 "data_requests.db-shm", "data_requests.json"):
        path = Path(base_dir) / name
        if path.exists():
            digest = hashlib.sha256()
            with open(str(path), "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            marks[name] = digest.hexdigest()
    return marks


def test_mcp_calls_are_readonly(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    server = _make_server(tmp_path)
    _call(server, "datareq_search", {"query": "딥페이크"})
    before = _fingerprints(tmp_path)
    _call(server, "datareq_search", {"query": "심의위"})
    _call(server, "datareq_get_document", {"doc_id": "DOC-2024-001"})
    _call(server, "datareq_get_qa", {"qa_id": "QA-2025-001"})
    _call(server, "datareq_ledger_list", {})
    _call(server, "datareq_ledger_get", {"ledger_id": "REQ-2026-001"})
    _call(server, "datareq_ledger_history", {"ledger_id": "REQ-2026-001"})
    _call(server, "datareq_evidence_pack", {"question": "딥페이크 대응"})
    _call(server, "datareq_status", {})
    assert _fingerprints(tmp_path) == before


def test_mcp_missing_db_does_not_rebuild(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    server = _make_server(empty)
    found = _call(server, "datareq_search", {"query": "딥페이크"})
    assert found["ok"] is False
    assert not (empty / "data_requests.db").exists()


def test_stdio_protocol_roundtrip_with_sdk_client(tmp_path):
    """독립 클라이언트(공식 SDK stdio)로 실제 프로세스 경계를 넘어 호출한다.

    서버 프로세스의 stdout은 JSON-RPC만이어야 하며, 배치·진단 출력이 섞이면
    프로토콜이 깨져 이 테스트가 실패한다.
    """
    import asyncio
    import sys
    from pathlib import Path
    agent_fixtures.build_synthetic_db(tmp_path)
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    repo_root = Path(__file__).resolve().parent.parent

    async def _run():
        # SDK stdio 클라이언트는 안전 허용 목록 환경변수만 상속하므로,
        # 데이터 루트는 env로 명시한다 (실제 MCP 호스트 설정도 동일).
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(repo_root / "scripts" / "datareq_mcp.py")],
            env={"DATAREQ_DATA_ROOT": str(tmp_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert EXPECTED_TOOLS <= names
                result = await session.call_tool(
                    "datareq_search", {"query": "딥페이크"})
                texts = [getattr(block, "text", "") for block in result.content]
                payload = json.loads("\n".join(texts))
                assert payload["ok"] is True
                assert payload["data"]["items"], "synthetic match expected"
                return names

    previous = os.environ.get("DATAREQ_DATA_ROOT")
    os.environ["DATAREQ_DATA_ROOT"] = str(tmp_path)
    try:
        names = asyncio.run(asyncio.wait_for(_run(), timeout=120))
    finally:
        if previous is None:
            del os.environ["DATAREQ_DATA_ROOT"]
        else:
            os.environ["DATAREQ_DATA_ROOT"] = previous
    assert EXPECTED_TOOLS <= names
