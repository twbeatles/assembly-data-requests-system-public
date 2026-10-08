# -*- coding: utf-8 -*-
"""Facade·CLI 결과 계약 테스트. 합성 픽스처만 쓴다 (운영 데이터 금지)."""

import agent_fixtures
from agent_bridge import policy
from agent_bridge.errors import DataReqError
from agent_bridge.facade import AgentFacade
from agent_bridge.models import RESULT_SCHEMA


def _facade(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    return AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])


def test_result_envelope_fixed_fields(tmp_path):
    facade = _facade(tmp_path)
    from agent_bridge.cli import build_parser, execute, _hoist_global_flags
    parser = build_parser()
    args = parser.parse_args(_hoist_global_flags(["search", "딥페이크", "--json"]))
    envelope, code = execute(args, base_dir=str(tmp_path))
    assert code == 0
    assert envelope["schema"] == RESULT_SCHEMA
    assert set(envelope) == {"schema", "ok", "request_id", "operation",
                             "data", "warnings", "error", "meta"}
    assert envelope["ok"] is True and envelope["error"] is None
    assert envelope["operation"] == "search"


def test_error_codes_and_exit_mapping(tmp_path):
    facade = _facade(tmp_path)
    try:
        facade.get_document("DOC-NOPE")
    except DataReqError as exc:
        assert exc.code == "invalid_input"
        assert exc.exit_code == 2
    else:
        raise AssertionError("unknown doc must fail")
    try:
        AgentFacade(base_dir=tmp_path, profile="superuser")
    except DataReqError as exc:
        assert exc.code == "invalid_input"
    else:
        raise AssertionError("unknown profile must fail")


def test_limit_and_chars_are_clamped(tmp_path):
    facade = _facade(tmp_path)
    assert policy.clamp_limit(10000) == policy.MAX_LIMIT
    assert policy.clamp_limit(None) == policy.DEFAULT_LIMIT
    found = facade.search("딥페이크", limit=10000)
    assert len(found["items"]) <= policy.MAX_LIMIT
    doc = facade.get_document("DOC-2026-001", max_chars=10 ** 9)
    assert len(doc["excerpt"]) <= policy.MAX_EXCERPT_CHARS


def test_path_allowlist_denies_escape(tmp_path):
    facade = _facade(tmp_path)
    try:
        facade.pipeline_plan(str(tmp_path.parent))
    except DataReqError as exc:
        assert exc.code == "policy_denied"
    else:
        raise AssertionError("parent dir must be denied")
    try:
        policy.resolve_allowed_root("/definitely/not/here/xyz", [tmp_path])
    except DataReqError as exc:
        assert exc.code == "policy_denied"
    else:
        raise AssertionError("outside root must be denied")


def test_ledger_and_history_masking(tmp_path):
    # 마스킹 정본은 기존 mask_pii다 (휴대폰·주민번호·이메일). 담당자 성명은
    # 요구자명과 같은 업무 데이터이므로 남긴다.
    facade = _facade(tmp_path)
    row = facade.ledger_get("REQ-2026-001")
    assert "010-1234-5678" not in str(row.get("aide"))
    assert "****" in str(row.get("aide"))
    history = facade.ledger_history("REQ-2026-001")
    assert history["history"], "history row expected"
    assert "010-1234-5678" not in history["history"][0]["snapshot"]
    assert "****" in history["history"][0]["snapshot"]


def test_cursor_pagination_stable(tmp_path):
    facade = _facade(tmp_path)
    first = facade.ledger_list(limit=2)
    assert first["next_cursor"] is not None
    second = facade.ledger_list(limit=2, cursor=first["next_cursor"])
    first_ids = [r["ledger_id"] for r in first["items"]]
    second_ids = [r["ledger_id"] for r in second["items"]]
    assert not set(first_ids) & set(second_ids)
