"""2026-10-13 boundary regressions. All data and child-process markers are synthetic."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

import agent_fixtures
import pytest
from agent_bridge import evidence
from agent_bridge.facade import AgentFacade
from db.database_manager import DatabaseManager
from db.ledger_outbox import copy_delivery_state
from db.migrations import SCHEMA_VERSION
from pipeline_guard import PipelineGuard
from services.ledger_service import LedgerService
from storage_activity import StorageBusyError, storage_activity


def _shadow_swap(base):
    db = base / "data_requests.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        ledgers = [dict(row) for row in conn.execute("SELECT * FROM request_ledger")]
    shadow = base / "shadow.db"
    with closing(sqlite3.connect(shadow)) as conn:
        DatabaseManager.init_schema(conn)
        DatabaseManager.insert_records(conn, [], [], ledgers)
        copy_delivery_state(db, conn)
        DatabaseManager.copy_ledger_history(db, conn)
    DatabaseManager.atomic_swap(shadow, db)


@pytest.mark.parametrize("action", ["insert", "update", "delete", "restore"])
def test_rebuild_drains_admitted_mutations(tmp_path, monkeypatch, action):
    agent_fixtures.build_synthetic_db(tmp_path)
    svc = LedgerService(base_dir=tmp_path)
    monkeypatch.setattr(svc, "sync_json_file", lambda **kw: None)
    monkeypatch.setattr(svc, "_bump_live_version", lambda: None)
    lid = "REQ-2026-001"
    if action == "restore":
        assert svc.delete_ledger_item(lid)["success"]
    checked, resume, started, acquired = (threading.Event() for _ in range(4))
    original = svc._reject_if_pipeline_running
    results, errors = [], []

    def pause():
        result = original()
        checked.set()
        assert resume.wait(10)
        return result

    monkeypatch.setattr(svc, "_reject_if_pipeline_running", pause)
    calls = {
        "insert": lambda: svc.insert_ledger_item(
            {
                "title": "new",
                "requester": "synthetic",
                "year": "2026",
                "idempotency_key": "round10",
            }
        ),
        "update": lambda: svc.update_ledger_item(lid, {"note": "after"}),
        "delete": lambda: svc.delete_ledger_item(lid),
        "restore": lambda: svc.restore_ledger_item(lid),
    }

    def mutate():
        try:
            results.append(calls[action]())
        except BaseException as exc:  # noqa: BLE001 - surfaced by the parent thread assertion
            errors.append(exc)

    def rebuild():
        guard = PipelineGuard(tmp_path)
        started.set()
        try:
            assert guard.acquire(False)
            acquired.set()
            _shadow_swap(tmp_path)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the parent thread assertion
            errors.append(exc)
        finally:
            guard.release()

    writer = threading.Thread(target=mutate)
    pipeline = threading.Thread(target=rebuild)
    writer.start()
    try:
        assert checked.wait(10)
        pipeline.start()
        assert started.wait(10)
        assert not acquired.wait(0.2), "pipeline must drain the admitted writer"
    finally:
        resume.set()
        writer.join(10)
        if pipeline.ident is not None:
            pipeline.join(10)
    assert not writer.is_alive() and not pipeline.is_alive()
    assert not errors, errors
    assert results[0]["success"]
    with closing(sqlite3.connect(tmp_path / "data_requests.db")) as conn:
        if action == "insert":
            lid = results[0]["item"]["ledger_id"]
            assert (
                conn.execute(
                    "SELECT ledger_id FROM ledger_receipts WHERE request_key='round10'"
                ).fetchone()[0]
                == lid
            )
        row = conn.execute(
            "SELECT note,status FROM request_ledger WHERE ledger_id=?", (lid,)
        ).fetchone()
        assert row is not None
        if action == "update":
            assert row[0] == "after"
        if action == "delete":
            assert row[1] == "[삭제]"
        if action == "restore":
            assert row[1] == "미제출"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM ledger_history WHERE ledger_id=?", (lid,)
            ).fetchone()[0]
            >= 1
        )
    queue = json.loads(
        (tmp_path / ".excel_pending_queue.json").read_text(encoding="utf-8")
    )
    assert any(op["item"]["ledger_id"] == lid for op in queue)


def test_activity_gate_is_process_wide_and_survives_kill(tmp_path):
    code = """
import sys,time
from pathlib import Path
from storage_activity import storage_activity
base=Path(sys.argv[1])
with storage_activity(base):
    (base/'ready').write_text('1')
    time.sleep(30)
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts"),
        DATAREQ_TEST_MODE="1",
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        with pytest.raises(StorageBusyError), storage_activity(tmp_path, timeout=0.1):
            pytest.fail("a different process owns the activity gate")
    finally:
        child.kill()
        child.communicate(timeout=10)
    with storage_activity(tmp_path, timeout=1):
        assert (tmp_path / ".storage_activity.guard").exists()


def test_revision_tracks_all_bodies_and_rollback(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    facade = AgentFacade(tmp_path)
    before = facade.dataset_version()
    with closing(sqlite3.connect(tmp_path / "data_requests.db")) as conn:
        conn.execute(
            "UPDATE documents SET full_markdown='changed' WHERE doc_id='DOC-2026-001'"
        )
        conn.commit()
        changed = facade.dataset_version()
        assert changed != before
        conn.execute(
            "UPDATE qa_items SET answer_markdown='changed QA' WHERE qa_id='QA-2026-001'"
        )
        conn.rollback()
        assert facade.dataset_version() == changed
        conn.execute(
            "UPDATE qa_items SET answer_markdown='changed QA' WHERE qa_id='QA-2026-001'"
        )
        conn.commit()
        assert facade.dataset_version() != changed


def test_revision_fingerprint_counts_tombstones_as_deleted(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    value = AgentFacade(tmp_path).dataset_version()
    assert value.startswith(f"v{SCHEMA_VERSION}-docs3-qa3-ledger4-tomb1-rev")


def test_async_json_keeps_request_when_activity_is_busy(tmp_path, monkeypatch):
    agent_fixtures.build_synthetic_db(tmp_path)
    attempts = []

    @contextmanager
    def busy_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise StorageBusyError("busy")
        yield

    monkeypatch.setattr("services.ledger.service.storage_activity", busy_once)
    svc = LedgerService(base_dir=tmp_path)
    thread = svc.sync_json_file(async_mode=True)
    thread.join(5)
    assert not thread.is_alive() and len(attempts) >= 2
    data = json.loads((tmp_path / "data_requests.json").read_text(encoding="utf-8"))
    assert len(data["documents"]) == 3


def test_readonly_service_rejects_json_output(tmp_path):
    svc = LedgerService(base_dir=tmp_path, read_only=True)
    with pytest.raises(RuntimeError):
        svc.sync_json_file()
    assert list(tmp_path.iterdir()) == []


def test_revision_survives_online_backup(tmp_path, monkeypatch):
    agent_fixtures.build_synthetic_db(tmp_path)
    facade = AgentFacade(tmp_path)
    db = tmp_path / "data_requests.db"
    before, inode = facade.dataset_version(), db.stat().st_ino
    new = tmp_path / "next"
    agent_fixtures.build_synthetic_db(new)
    shadow = new / "data_requests.db"
    with closing(sqlite3.connect(shadow)) as conn:
        conn.execute(
            "UPDATE documents SET full_markdown='after-body' WHERE doc_id='DOC-2026-001'"
        )
        conn.commit()
    # Actual Windows open handle selects the fallback; other platforms exercise
    # SQLite backup with only the file-replace operation injected as busy.
    with closing(sqlite3.connect(db)) as holder:
        holder.execute("SELECT COUNT(*) FROM documents").fetchone()
        if os.name != "nt":
            monkeypatch.setattr(
                "db.database_manager.os.replace",
                lambda *args: (_ for _ in ()).throw(PermissionError("busy")),
            )
        DatabaseManager.atomic_swap(shadow, db)
        assert db.stat().st_ino == inode
        assert facade.get_document("DOC-2026-001")["excerpt"] == "after-body"
        assert facade.dataset_version() != before


def test_v3_migrates_without_dropping_content(tmp_path):
    agent_fixtures.build_synthetic_db(tmp_path)
    with closing(sqlite3.connect(tmp_path / "data_requests.db")) as conn:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'revision_%'"
        ).fetchall():
            conn.execute('DROP TRIGGER "' + name + '"')
        conn.execute("DROP TABLE dataset_revision")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        DatabaseManager.ensure_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
        assert conn.execute("SELECT build_id FROM dataset_revision").fetchone()[0]


def test_evidence_reads_one_snapshot_while_unmanaged_writer_commits(
    tmp_path, monkeypatch
):
    agent_fixtures.build_synthetic_db(tmp_path)
    facade = AgentFacade(tmp_path)
    with closing(sqlite3.connect(tmp_path / "data_requests.db")) as writer:
        # Hold a live WAL connection, so the read transaction is a genuine SQLite
        # snapshot even for SQL tools that do not participate in our activity gate.
        writer.execute("SELECT COUNT(*) FROM documents").fetchone()
        before = facade.dataset_version()
        old = facade.get_document("DOC-2026-001")["excerpt"]
        fetch = evidence._fetch_source
        counter = []

        def committing_fetch(snapshot, source_id):
            item = fetch(snapshot, source_id)
            counter.append(source_id)
            if len(counter) == 1:
                writer.execute(
                    "UPDATE documents SET full_markdown='new-body' WHERE doc_id='DOC-2026-001'"
                )
                writer.commit()
            return item

        monkeypatch.setattr(evidence, "_fetch_source", committing_fetch)
        pack = facade.evidence_pack(
            "synthetic", selected_ids=["doc:DOC-2026-001", "doc:DOC-2026-001"]
        )
        assert [source["excerpt"] for source in pack["sources"]] == [old, old]
        assert pack["source_dataset"]["db_fingerprint"] == before
        assert facade.get_document("DOC-2026-001")["excerpt"] == "new-body"
        assert facade.dataset_version() != before


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_src_url_normalization_and_clipboard_fallback(tmp_path):
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/templates/dashboard/js/05_sanitize.js"
    )
    harness = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
for(const src of ['http://example.invalid/x','https://example.invalid/x','//example.invalid/x','///example.invalid/x',String.raw`\\example.invalid\x`,String.raw`/\example.invalid/x`,'&#47;&#47;example.invalid/x','&#92;&#92;example.invalid/x',' / /example.invalid/x']) {
  assert(!context.sanitizeHtml('<img src="'+src+'">').includes('src='),src);
}
for(const src of ['images/local.png','/images/local.png','data:image/png;base64,AAAA']) {
  assert(context.sanitizeHtml('<img src="'+src+'">').includes('src='),src);
}
const copied=[];
context.copyText=(text,message)=>copied.push([text,message]);
context.navigator={};context.window={};
context.copyTableHtml({closest:()=>({querySelector:()=>({outerHTML:'<table></table>',innerText:'synthetic'})})});
assert.equal(copied[0][0],'synthetic');assert(copied[0][1].includes('서식'));
"""
    script = tmp_path / "regression.js"
    script.write_text(harness, encoding="utf-8")
    run = subprocess.run(
        [shutil.which("node"), str(script), str(source)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("outcome", ["partial", "timeout", "empty"])
def test_failed_conversion_keeps_last_valid_body_and_hash(
    tmp_path, monkeypatch, outcome
):
    import parse_all

    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"synthetic source")
    output = tmp_path / "parsed"
    monkeypatch.setattr(parse_all, "ROOT_DIR", tmp_path)
    body, cache = parse_all.parse_output_paths(source, output)
    body.parent.mkdir(parents=True)
    body.write_text("last valid full body", encoding="utf-8")
    cache.write_text("old source hash", encoding="utf-8")

    def failing_run(*args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("synthetic converter", 1)
        if outcome == "partial":
            body.write_text("truncated", encoding="utf-8")
        return subprocess.CompletedProcess(
            "synthetic converter",
            1 if outcome == "partial" else 0,
            stdout="",
            stderr="synthetic failure",
        )

    monkeypatch.setattr(parse_all, "run_converter", failing_run)
    result = parse_all.parse_single_file(source, output, force=True)
    assert not result["success"]
    assert body.read_text(encoding="utf-8") == "last valid full body"
    assert cache.read_text(encoding="utf-8") == "old source hash"
    assert not list(body.parent.glob("*.previous"))


def test_interrupted_conversion_restores_matching_body_and_hash(tmp_path):
    from pipeline.parse.output import recover_parse_output

    body, cache = tmp_path / "doc.md", tmp_path / "doc.md.sha256"
    body.with_name(body.name + ".previous").write_text("valid", encoding="utf-8")
    cache.with_name(cache.name + ".previous").write_text("valid hash", encoding="utf-8")
    body.write_text("partial after interruption", encoding="utf-8")
    cache.write_text("new incorrect hash", encoding="utf-8")
    recover_parse_output(body, cache)
    assert body.read_text(encoding="utf-8") == "valid"
    assert cache.read_text(encoding="utf-8") == "valid hash"


def test_output_backup_failure_does_not_delete_valid_source(tmp_path, monkeypatch):
    from pipeline.parse.output import parse_output_transaction

    body, cache = tmp_path / "doc.md", tmp_path / "doc.md.sha256"
    body.write_text("valid", encoding="utf-8")
    cache.write_text("valid hash", encoding="utf-8")
    monkeypatch.setattr(
        "pipeline.parse.output.os.replace",
        lambda *args: (_ for _ in ()).throw(PermissionError("locked")),
    )
    with pytest.raises(PermissionError), parse_output_transaction(body, cache):
        pytest.fail("must not convert without preserving previous output")
    assert body.read_text(encoding="utf-8") == "valid"
    assert cache.read_text(encoding="utf-8") == "valid hash"


def test_derived_output_failure_reports_partial_state_and_retry(tmp_path, monkeypatch):
    import add_documents_smart as ad

    monkeypatch.setattr(ad, "SYSTEM_DIR", tmp_path)
    monkeypatch.setattr(ad, "DROP_DIRS", [])
    monkeypatch.setattr(ad, "IS_DATA_DIST_ENV", False)
    monkeypatch.setattr(ad, "IS_DIST_ENV", True)
    monkeypatch.setattr(ad.parse_all, "main", lambda: {"success": True})
    monkeypatch.setattr(ad.extract_and_build_db, "main", lambda: {"success": True})
    monkeypatch.setattr(ad.generate_excel_db, "main", lambda: {"success": True})
    page = tmp_path / "synthetic.html"
    page.write_text("previous HTML", encoding="utf-8")
    monkeypatch.setattr(
        ad.generate_web_dashboard,
        "main",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    failed = ad._run_smart_add_body()
    assert not failed["success"] and failed["artifacts_may_be_partial"]
    assert failed["failed_step"].startswith("4.")
    assert len(failed["completed_steps"]) == 3
    assert page.read_text(encoding="utf-8") == "previous HTML"

    def retry_html():
        page.write_text("current HTML", encoding="utf-8")
        return {"success": True}

    monkeypatch.setattr(ad.generate_web_dashboard, "main", retry_html)
    retried = ad._run_smart_add_body()
    assert retried["success"] and not retried["artifacts_may_be_partial"]
    assert page.read_text(encoding="utf-8") == "current HTML"


def test_non_windows_file_open_does_not_report_fake_success(tmp_path, monkeypatch):
    from web import downloads

    monkeypatch.setattr(downloads.sys, "platform", "linux")
    result = downloads.open_in_system(tmp_path / "synthetic.pdf")
    assert not result["success"] and result["code"] == "unsupported_platform"


def test_watcher_sync_is_drained_before_rebuild(tmp_path, monkeypatch):
    from openpyxl import Workbook
    from services.excel_sync_service import ExcelSyncService

    agent_fixtures.build_synthetic_db(tmp_path)
    book = Workbook()
    sheet = book.active
    sheet.title = "2026"
    sheet.append(["대장ID", "연번", "의원명", "요구자료명", "제출", "비고"])
    sheet.append(
        ["REQ-2026-001", "1", "김철수", "딥페이크 대응 요구", "미제출", "excel-after"]
    )
    path = tmp_path / "synthetic.xlsx"
    book.save(path)
    book.close()
    (tmp_path / "config.json").write_text(
        json.dumps({"master_excel": str(path)}), encoding="utf-8"
    )
    sync = ExcelSyncService(base_dir=tmp_path)
    checked, resume, started, acquired = (threading.Event() for _ in range(4))
    original = sync._pipeline_block
    results, errors = [], []

    def pause():
        blocked = original()
        checked.set()
        assert resume.wait(10)
        return blocked

    def write():
        try:
            results.append(sync.sync_excel_to_db(write_json=False))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the parent thread assertion
            errors.append(exc)

    def rebuild():
        guard = PipelineGuard(tmp_path)
        started.set()
        try:
            guard.acquire(False)
            acquired.set()
            _shadow_swap(tmp_path)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the parent thread assertion
            errors.append(exc)
        finally:
            guard.release()

    monkeypatch.setattr(sync, "_pipeline_block", pause)
    watcher, pipeline = threading.Thread(target=write), threading.Thread(target=rebuild)
    watcher.start()
    try:
        assert checked.wait(10)
        pipeline.start()
        assert started.wait(10)
        assert not acquired.wait(0.2)
    finally:
        resume.set()
        watcher.join(10)
        if pipeline.ident is not None:
            pipeline.join(10)
    assert not errors, errors
    assert results[0]["success"]
    with closing(sqlite3.connect(tmp_path / "data_requests.db")) as conn:
        assert (
            conn.execute(
                "SELECT note FROM request_ledger WHERE ledger_id='REQ-2026-001'"
            ).fetchone()[0]
            == "excel-after"
        )
