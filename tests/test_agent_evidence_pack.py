# -*- coding: utf-8 -*-
"""Evidence Pack v1 생성·검증 테스트. 합성 픽스처만 쓴다 (운영 데이터 금지)."""

import sqlite3

import agent_fixtures
from agent_bridge.errors import DataReqError
from agent_bridge.evidence import build_pack, verify_pack
from agent_bridge.facade import AgentFacade


def _facade(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    return AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])


def test_pack_tracks_sources_and_hashes(tmp_path):
    facade = _facade(tmp_path)
    pack = build_pack(facade, "딥페이크 대응 방안은?",
                      selected_ids=["doc:DOC-2026-001", "qa:QA-2026-001"])
    assert pack["schema"] == "datareq-evidence-pack/v1"
    assert pack["claims"][0]["state"] == "needs_review"
    assert len(pack["sources"]) == 2
    for source in pack["sources"]:
        assert source["content_sha256"].startswith("sha256:")
        assert source["verification"] == "source_text_match"
    assert pack["redactions"]["applied"] is True
    result = verify_pack(facade, pack)
    assert result["ok"] is True


def test_pack_detects_stale_source(tmp_path):
    facade = _facade(tmp_path)
    pack = build_pack(facade, "딥페이크 대응", selected_ids=["doc:DOC-2026-001"])
    conn = sqlite3.connect(str(tmp_path / "data_requests.db"))
    try:
        conn.execute("UPDATE documents SET full_markdown = ? WHERE doc_id = ?",
                     ("전면 개정된 본문이다.", "DOC-2026-001"))
        conn.commit()
    finally:
        conn.close()
    # FTS 트리거가 본체 변경을 따라가므로 검색 결과는 그대로, 해시만 어긋난다.
    result = verify_pack(facade, pack)
    assert result["ok"] is False
    assert result["results"][0]["state"] == "source_stale"


def test_pack_without_evidence_marks_unresolved(tmp_path):
    facade = _facade(tmp_path)
    pack = build_pack(facade, "존재하지않는질문xyz", selected_ids=["doc:DOC-NOPE"])
    assert pack["sources"] == []
    assert pack["unresolved"], "근거 없음 표기가 있어야 한다"
    for claim in pack["claims"]:
        assert claim["state"] == "needs_review"


def test_empty_question_rejected(tmp_path):
    facade = _facade(tmp_path)
    try:
        build_pack(facade, "   ")
    except DataReqError as exc:
        assert exc.code == "invalid_input"
    else:
        raise AssertionError("empty question must fail")
