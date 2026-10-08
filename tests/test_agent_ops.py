# -*- coding: utf-8 -*-
"""운영·manifest 회귀 (PR-06). 합성 픽스처만 쓴다 (운영 데이터 금지).

- Job 관찰: 생성·상태 전이·TTL 만료.
- 산출물 manifest: 지문 생성·검증 roundtrip, 변조 탐지.
- 정합성: DB↔JSON 불일치 탐지 (읽기 전용).
- 전문 보존: 단건 조회를 이어 읽으면 정본 전체와 한 글자도 다르지 않음.
- 파이프라인 계획: 파일 이동·DB 변경 없음.
"""

import json
import sqlite3

import agent_fixtures
from agent_bridge import artifacts, jobs
from agent_bridge.facade import AgentFacade


def _facade(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    return AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])


def test_job_lifecycle_and_ttl():
    job = jobs.create_job("pipeline_plan", {"input": "somewhere"})
    assert job["status"] == "planned"
    updated = jobs.update_job(job["job_id"], "done", step="scanned")
    assert updated is not None
    assert updated["status"] == "done"
    assert updated["steps"] == ["scanned"]
    fetched = jobs.get_job(job["job_id"])
    assert fetched is not None and fetched["job_id"] == job["job_id"]
    assert jobs.get_job("job-nonexistent") is None
    jobs._JOBS[job["job_id"]]["created_at"] -= (jobs.TTL_SECONDS + 1)
    assert jobs.get_job(job["job_id"]) is None


def test_artifact_manifest_roundtrip_and_tamper(tmp_path):
    target = tmp_path / "payload.txt"
    target.write_text("배포 산출물", encoding="utf-8")
    mark = artifacts.fingerprint_file(target)
    assert mark["available"] is True
    ok = artifacts.verify_manifest([{"path": str(target),
                                     "sha256": mark["sha256"]}])
    assert ok["ok"] is True
    target.write_text("배포 산출물 변조", encoding="utf-8")
    tampered = artifacts.verify_manifest([{"path": str(target),
                                           "sha256": mark["sha256"]}])
    assert tampered["ok"] is False
    missing = artifacts.fingerprint_file(tmp_path / "nope.bin")
    assert missing["available"] is False


def test_reconcile_detects_mismatch(tmp_path):
    facade = _facade(tmp_path)
    clean = facade.reconcile()
    assert clean["db"]["available"] is True
    assert clean["mismatch"] is True  # 픽스처 JSON은 빈 캐시이므로 불일치가 정상
    assert clean["db"]["documents"] == 3
    assert clean["json"]["documents"] == 0


def test_full_text_roundtrip_through_paging(tmp_path):
    facade = _facade(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "data_requests.db"))
    try:
        expected = conn.execute(
            "SELECT full_markdown FROM documents WHERE doc_id = ?",
            ("DOC-2024-001",)).fetchone()[0]
    finally:
        conn.close()
    collected = []
    offset = 0
    while True:
        page = facade.get_document("DOC-2024-001", max_chars=100, offset=offset)
        collected.append(page["excerpt"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(collected) == expected


def test_pipeline_plan_changes_nothing(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "신규 문서.hwp").write_bytes(b"fake-hwp-bytes")
    facade = AgentFacade(base_dir=tmp_path, allowed_roots=[tmp_path])
    before = {(p.name, p.stat().st_size) for p in inbox.iterdir()}
    plan = facade.pipeline_plan(str(inbox))
    assert plan["file_count"] == 1
    assert plan["note"].startswith("계획만")
    after = {(p.name, p.stat().st_size) for p in inbox.iterdir()}
    assert before == after
    conn = sqlite3.connect(str(tmp_path / "data_requests.db"))
    try:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        conn.close()
    assert docs == 3
