"""Regression scenarios use disposable workbooks/DBs and real client functions."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import pytest
from db.database_manager import DatabaseManager
from db.ledger_outbox import read_operations, copy_delivery_state
from db.migrations import SCHEMA_VERSION, SchemaTooNewError
from db.preflight import check_database_version, snapshot_before_rebuild
from pipeline_guard import PipelineGuard, STALE_SECONDS
from services.excel_sync_service import ExcelSyncService
from services.ledger_service import LedgerService
from test_audit_phase8_fixes import LedgerSyncFixtureMixin


class TestDurableEdits(LedgerSyncFixtureMixin, unittest.TestCase):
    def test_delete_restore_survive_failed_queue_storage_in_order(self):
        excel = self.make_excel([["REQ-2026-001", "1", "김의원", "자료", "작성중", "일반", "원본"]])
        sync = self.sync_service()
        sync.sync_excel_to_db()
        ledger = self.ledger_service()
        with mock.patch.object(sync, "_save_pending_queue", return_value=False):
            assert ledger.delete_ledger_item("REQ-2026-001")["excel_pending"]
            assert ledger.restore_ledger_item("REQ-2026-001")["excel_pending"]
        LedgerService.wait_last_sync(3)
        restarted = ExcelSyncService(base_dir=self.tmp, db_path=self.db_path)
        assert restarted.pending_count() == 2
        assert restarted.flush_pending_queue() == 2
        assert self.excel_rows(excel)[0]["제출"] == "작성중"
        assert self.excel_rows(excel)[0]["비고"] == "원본"
        assert restarted.pending_count() == 0

    def test_queue_disk_failure_restart_preserves_edit_and_replays(self):
        excel = self.make_excel([["REQ-2026-001", "1", "김의원", "자료", "작성중", "일반", "원본"]])
        sync = self.sync_service()
        sync.sync_excel_to_db()
        with mock.patch.object(sync, "_save_pending_queue", return_value=False):
            result = self.ledger_service().update_ledger_item("REQ-2026-001", {"note": "웹 수정"})
        LedgerService.wait_last_sync(3)
        assert result["success"] and result["excel_pending"]
        assert len(read_operations(self.db_path)) == 1
        restarted = ExcelSyncService(base_dir=self.tmp, db_path=self.db_path)
        assert restarted.protected_field_map()["REQ-2026-001"] == {"note"}
        restarted.sync_excel_to_db(write_json=False)
        assert self.db_rows()["REQ-2026-001"]["note"] == "웹 수정"
        assert restarted.flush_pending_queue() == 1
        assert self.excel_rows(excel)[0]["비고"] == "웹 수정"
        assert not read_operations(self.db_path)
        assert restarted.pending_count() == 0

    def test_outbox_insert_failure_rolls_back_db_edit(self):
        self.make_excel([["REQ-2026-001", "1", "김의원", "자료", "작성중", "일반", "원본"]])
        self.sync_service().sync_excel_to_db()
        with mock.patch("services.ledger.service.enqueue_excel", side_effect=sqlite3.OperationalError("disk full")):
            result = self.ledger_service().update_ledger_item("REQ-2026-001", {"note": "유실되면 안 됨"})
        assert not result["success"]
        assert self.db_rows()["REQ-2026-001"]["note"] == "원본"

    def test_duplicate_registration_after_lost_response_has_one_row(self):
        ledger = self.ledger_service()
        data = dict(year="2026", requester="김의원", title="멱등 등록", idempotency_key="retry-1")
        one = ledger.insert_ledger_item(data)
        two = ledger.insert_ledger_item(data)
        LedgerService.wait_last_sync(3)
        assert one["success"] and two["success"] and two["replayed"]
        assert one["item"]["ledger_id"] == two["item"]["ledger_id"]
        assert len(self.db_rows()) == 1
        changed = ledger.insert_ledger_item(dict(data, title="다른 요청"))
        assert not changed["success"] and changed["code"] == "conflict"

    def test_rebuild_copies_pending_intent_and_registration_receipt(self):
        sync = self.sync_service()
        with mock.patch.object(sync, "_save_pending_queue", return_value=False):
            result = self.ledger_service().insert_ledger_item(dict(year="2026", requester="의원", title="보관", idempotency_key="k"))
        LedgerService.wait_last_sync(3)
        assert result["success"]
        with sqlite3.connect(":memory:") as dest:
            DatabaseManager.ensure_schema(dest)
            copy_delivery_state(self.db_path, dest)
            assert dest.execute("SELECT COUNT(*) FROM ledger_outbox").fetchone()[0] == 1
            assert dest.execute("SELECT COUNT(*) FROM ledger_receipts").fetchone()[0] == 1


def test_future_schema_rebuild_refuses_before_any_processing(tmp_path, monkeypatch):
    import extract_and_build_db as builder
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        conn.execute("CREATE TABLE future_only (value TEXT)")
        conn.execute("INSERT INTO future_only VALUES ('keep')")
    original = db.read_bytes()
    monkeypatch.setattr(builder, "DB_PATH", db)
    with mock.patch.object(builder, "_main_locked") as build:
        result = builder.main()
    assert not result["success"]
    build.assert_not_called()
    assert db.read_bytes() == original
    with sqlite3.connect(db) as conn:
        with pytest.raises(SchemaTooNewError):
            DatabaseManager.ensure_schema(conn)
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("future_only",)]
    with pytest.raises(SchemaTooNewError):
        LedgerService(db_path=db, base_dir=tmp_path)._ensure_schema()
    assert db.read_bytes() == original


def test_snapshot_includes_wal_and_keeps_distinct_backups(tmp_path):
    db = tmp_path / "live.db"
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE value (x)")
        writer.execute("INSERT INTO value VALUES (123)")
        writer.commit()
        first = snapshot_before_rebuild(db)
        second = snapshot_before_rebuild(db)
        assert first != second
        assert first is not None
        with sqlite3.connect(first) as saved:
            assert saved.execute("SELECT x FROM value").fetchone()[0] == 123


def test_live_old_lock_and_empty_publication_are_not_stale(tmp_path):
    guard = PipelineGuard(tmp_path)
    assert guard.acquire()
    old = time.time() - STALE_SECONDS - 10
    os.utime(guard.path, (old, old))
    assert not guard.is_stale()
    assert guard.is_locked()
    # Restore identity after simulating age; an unrelated object cannot release it.
    guard._identity = guard._signature()
    PipelineGuard(tmp_path).release()
    assert guard.path.exists()
    guard.release()
    guard.path.write_text("", encoding="utf-8")
    assert guard.is_locked()
    with pytest.raises(RuntimeError):
        PipelineGuard(tmp_path).acquire()


def test_lock_rejects_other_thread_and_process_then_recovers(tmp_path):
    guard = PipelineGuard(tmp_path)
    assert guard.acquire()
    result = []
    def competitor():
        try:
            PipelineGuard(tmp_path).acquire()
        except RuntimeError:
            result.append("blocked")
    thread = threading.Thread(target=competitor)
    thread.start()
    thread.join(5)
    assert result == ["blocked"]
    code = "from pipeline_guard import PipelineGuard; import sys; g=PipelineGuard(sys.argv[1]); g.acquire(False)"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts"))
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    assert child.returncode != 0
    guard.release()
    child = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    assert child.returncode == 0
    recovered = PipelineGuard(tmp_path)
    assert recovered.acquire(False)
    recovered.release()


def test_other_process_observes_incomplete_lock_as_busy(tmp_path):
    code = """from pipeline_guard import PipelineGuard
import sys,time
g=PipelineGuard(sys.argv[1])
with g._metadata_lock():
    g.path.write_text('',encoding='utf-8')
    print('ready',flush=True)
    time.sleep(30)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts"))
    child = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == b"ready"
        guard = PipelineGuard(tmp_path)
        assert guard.is_locked()
        with pytest.raises(RuntimeError):
            guard.acquire(False)
    finally:
        child.kill()
        child.communicate(timeout=5)
    old = time.time() - STALE_SECONDS - 1
    os.utime(guard.path, (old, old))
    assert guard.acquire(False)
    guard.release()


def test_migration_retry_does_not_duplicate_conflict(tmp_path):
    from migrate_existing_install import migrate_markdown
    source, dest = tmp_path / "old", tmp_path / "new"
    for root, text in ((source, "old body"), (dest, "new body")):
        folder = root / "_parsed_markdown"
        folder.mkdir(parents=True)
        (folder / "same.md").write_text(text, encoding="utf-8")
    assert migrate_markdown(source, dest)["copied"] == 1
    assert migrate_markdown(source, dest)["skipped"] == 1
    assert len(list((dest / "_parsed_markdown").glob("*.md"))) == 2


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_client_edit_keeps_original_version_and_empty_list_clears(tmp_path):
    node = shutil.which("node")
    assert node is not None
    root = Path(__file__).resolve().parents[1]
    src = (root / "scripts/templates/dashboard/js/10_ledger_crud.js").read_text(encoding="utf-8")
    harness = r'''
const assert = require('assert');
const nodes = {};
const document = {getElementById(id) { return nodes[id] ||= {value:'',innerText:'',classList:{add(){},remove(){}},querySelectorAll(){return []},options:[]}; }};
const window = {location:{protocol:'http:'}};
const localStorage = {getItem(){return '[]'}};
let currentSelectedLedgerItem = {ledger_id:'REQ-2026-001',requester:'a',title:'old',updated_at:'v1'};
let REQUEST_LEDGER = [currentSelectedLedgerItem], LEDGER_MAP = {'REQ-2026-001':currentSelectedLedgerItem};
function ledgerWriteBlockReason(){return ''} function notify(){} function render(){}
function mergeLocalLedger(rows){return rows.concat([{ledger_id:'LOCAL-kept'}])}
setSelectPreservingValue = () => {};
(async () => {
 openEditLedgerModal();
 nodes['edit-title'].value = 'my draft';
 LEDGER_MAP['REQ-2026-001'] = {...currentSelectedLedgerItem,title:'someone else',updated_at:'v2'};
 let sent;
 global.fetch = async (url, opts) => {
   if (opts) {sent=JSON.parse(opts.body); return {status:409,ok:false,json:async()=>({conflict:true,error:'conflict'})};}
   return {ok:true,json:async()=>({success:true,data:[]})};
 };
 await submitEditLedgerItemNow();
 assert.equal(sent.expected_updated_at,'v1');
 assert.equal(nodes['edit-title'].value,'my draft');
 assert.deepEqual(REQUEST_LEDGER.map(x=>x.ledger_id),['LOCAL-kept']);
 console.log('ok');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    js = tmp_path / "client.js"
    from test_audit_phase10_dashboard import extract_js
    boot = (root / "scripts/templates/dashboard/js/00_boot.js").read_text(encoding="utf-8")
    js.write_text(extract_js(boot, "applyServerLedger") + "\n" + src + "\n" + harness, encoding="utf-8")
    run = subprocess.run([node, str(js)], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert run.returncode == 0, run.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
@pytest.mark.parametrize("api_ok", [True, False])
def test_http_boot_empty_authority_and_network_fallback(tmp_path, api_ok):
    node = shutil.which("node")
    assert node is not None
    from template_renderer import DashboardRenderer
    root = Path(__file__).resolve().parents[1]
    boot = (root / "scripts/templates/dashboard/js/00_boot.js").read_text(encoding="utf-8")
    raw = dict(documents=[], qa_items=[], request_ledger=[dict(ledger_id="REQ-old")])
    b64 = DashboardRenderer.compress_payload(raw)
    code = f"""
const assert = require('assert');
const block = {{textContent:{json.dumps(b64)}}};
const document = {{getElementById:id=>id==='COMPRESSED_DATA'?block:null,querySelectorAll:()=>[]}};
const window = {{location:{{protocol:'http:'}}}};
let REQUEST_LEDGER=[], LEDGER_MAP={{}};
function mergeLocalLedger(rows) {{return rows.concat([{{ledger_id:'LOCAL-kept'}}]);}}
function checkAndNotifyOfflineSync() {{}}
const fetch = async()=>({{ok:{json.dumps(api_ok)},json:async()=>({{success:true,data:[]}})}});
{boot}
(async()=>{{
 await loadAndDecompressDatabase();
 assert.deepEqual(REQUEST_LEDGER.map(x=>x.ledger_id),{json.dumps(['LOCAL-kept'] if api_ok else ['REQ-old', 'LOCAL-kept'])});
}})().catch(e=>{{console.error(e);process.exit(1)}});
"""
    path = tmp_path / "boot.js"
    path.write_text(code, encoding="utf-8")
    result = subprocess.run([node, str(path)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
