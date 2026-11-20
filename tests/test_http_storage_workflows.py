"""Real HTTP → ledger service → SQLite/outbox → XLSX/JSON workflows.

Only the workbook save failure is injected. Routing, security, transactions,
queue persistence, replay and exports execute production code in tmp_path.
"""

import json
import sqlite3
import threading
import time
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import openpyxl
import pytest
import web_server
from db.database_manager import DatabaseManager
from services.excel_sync_service import ExcelSyncService
from services.ledger_service import LedgerService
from services.sync_service import SyncService


@pytest.fixture
def live_ledger(tmp_path, monkeypatch):
    db = tmp_path / "data_requests.db"
    with closing(sqlite3.connect(db)) as conn:
        DatabaseManager.init_schema(conn)
    excel = tmp_path / "국회 요구자료 목록.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "2026"
    wb.active.append(
        [
            "연번",
            "소속",
            "의원",
            "보좌관",
            "요구자료",
            "세부내역",
            "요구일",
            "마감일",
            "제출일",
            "부서",
            "제출",
            "비고",
            "요청형태",
            "대장ID",
        ]
    )
    wb.save(excel)
    wb.close()
    monkeypatch.setattr(web_server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(web_server, "DB_PATH", db)
    monkeypatch.setattr(web_server, "JSON_PATH", tmp_path / "data_requests.json")
    monkeypatch.setattr(web_server, "_cached_ledger_service", None)
    monkeypatch.setattr(web_server, "_cached_service_key", None)
    sync = ExcelSyncService(base_dir=tmp_path)
    monkeypatch.setattr(ExcelSyncService, "_instance", sync)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.RequestLedgerHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class Client:
        base = tmp_path

        def request(self, method, path, payload=None, *, raw=None, headers=None):
            body = (
                raw
                if raw is not None
                else (
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if payload is not None
                    else None
                )
            )
            origin = f"http://127.0.0.1:{server.server_port}"
            request_headers = {"Content-Type": "application/json", "Origin": origin}
            request_headers.update(headers or {})
            with closing(
                HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            ) as conn:
                conn.request(method, path, body=body, headers=request_headers)
                response = conn.getresponse()
                data = response.read()
                assert int(response.getheader("Content-Length")) == len(data)
                assert response.getheader("Content-Type").startswith("application/json")
                return response.status, json.loads(data)

        def rows(self, table):
            assert table in {
                "request_ledger",
                "ledger_history",
                "ledger_outbox",
                "ledger_receipts",
                "dataset_revision",
            }
            with closing(sqlite3.connect(db)) as conn:
                conn.row_factory = sqlite3.Row
                return [
                    dict(row)
                    for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
                ]

        def workbook_rows(self):
            book = openpyxl.load_workbook(excel, data_only=True)
            try:
                return [
                    tuple(row)
                    for sheet in book
                    for row in sheet.iter_rows(min_row=2, values_only=True)
                ]
            finally:
                book.close()

        def exported(self):
            # Wait for the actual asynchronous exporter, then read its artifact.
            LedgerService.wait_last_sync(timeout=10)
            import services.ledger.service as ledger_module

            thread = ledger_module.LAST_SYNC_THREAD
            assert thread is None or not thread.is_alive(), "JSON export did not finish"
            return json.loads(
                (tmp_path / "data_requests.json").read_text(encoding="utf-8")
            )

    client = Client()
    client.sync = sync
    client.excel = excel
    try:
        yield client
    finally:
        server.shutdown()
        server.server_close()
        worker.join(10)
        LedgerService.wait_last_sync(timeout=10)
        sync.stop_file_watcher()
        assert not worker.is_alive()


def _create(client):
    payload = {
        "year": "2026",
        "requester": "합성 의원실",
        "title": "실제 HTTP 자료요구",
        "note": "원래 비고",
        "idempotency_key": "http-registration",
    }
    status, result = client.request("POST", "/api/ledger", payload)
    assert status == 200 and result["success"] and result["excel_synced"], result
    return payload, result["item"]


def test_http_crud_retry_conflict_and_restore_reach_all_stores(live_ledger):
    client = live_ledger
    payload, item = _create(client)
    lid = item["ledger_id"]
    path = f"/api/ledger/{lid}"
    status, retry = client.request("POST", "/api/ledger", payload)
    assert status == 200 and retry["replayed"] and retry["item"]["ledger_id"] == lid
    status, conflict = client.request(
        "POST", "/api/ledger", {**payload, "title": "같은 키 다른 입력"}
    )
    assert status == 409 and not conflict["success"]
    assert (
        len(client.rows("request_ledger")) == len(client.rows("ledger_receipts")) == 1
    )
    assert len(client.workbook_rows()) == 1
    assert [r["action"] for r in client.rows("ledger_history")].count("INSERT") == 1

    status, edited = client.request(
        "PUT", path, {"note": "웹 수정", "expected_updated_at": item["updated_at"]}
    )
    assert status == 200 and edited["success"] and edited["excel_synced"], edited
    before = {
        t: client.rows(t)
        for t in (
            "request_ledger",
            "ledger_history",
            "ledger_outbox",
            "dataset_revision",
        )
    }
    status, stale = client.request(
        "PUT",
        path,
        {"note": "덮어쓰면 안 됨", "expected_updated_at": item["updated_at"]},
    )
    assert status == 409 and not stale["success"]
    assert {t: client.rows(t) for t in before} == before
    assert client.rows("request_ledger")[0]["note"] == "웹 수정"
    assert client.workbook_rows()[0][11] == "웹 수정"
    assert client.exported()["request_ledger"][0]["note"] == "웹 수정"

    status, deleted = client.request("DELETE", path)
    assert status == 200 and deleted["success"] and deleted["excel_synced"], deleted
    assert client.rows("request_ledger")[0]["status"] == "[삭제]"
    assert client.workbook_rows()[0][10] == "[삭제]"
    assert client.exported()["request_ledger"] == []
    status, restored = client.request("POST", path + "/restore")
    assert status == 200 and restored["success"] and restored["excel_synced"], restored
    assert client.rows("request_ledger")[0]["note"] == "웹 수정"
    assert client.rows("request_ledger")[0]["status"] != "[삭제]"
    assert len(client.workbook_rows()) == 1
    assert client.workbook_rows()[0][10] != "[삭제]"
    assert [r["ledger_id"] for r in client.exported()["request_ledger"]] == [lid]
    assert client.rows("ledger_outbox") == []


@pytest.mark.parametrize(
    "raw,headers,expected",
    [
        (b'{"title":', {}, 400),
        (b"[]", {}, 400),
        (b'{"title":"missing requester"}', {}, 400),
        (b"{}", {"Content-Length": str(5 * 1024 * 1024 + 1)}, 413),
        (
            b'{"title":"valid","requester":"office","year":"2026"}',
            {"Origin": "null"},
            403,
        ),
        (
            b'{"title":"valid","requester":"office","year":"2026"}',
            {"Origin": "http://127.0.0.1:1"},
            403,
        ),
        (
            b'{"title":"valid","requester":"office","year":"2026"}',
            {"Host": "attacker.example"},
            403,
        ),
    ],
)
def test_http_rejected_writes_leave_no_persistent_effect(
    live_ledger, raw, headers, expected
):
    client = live_ledger
    before = {
        t: client.rows(t)
        for t in (
            "request_ledger",
            "ledger_history",
            "ledger_outbox",
            "ledger_receipts",
            "dataset_revision",
        )
    }
    excel_before = client.excel.read_bytes()
    status, result = client.request("POST", "/api/ledger", raw=raw, headers=headers)
    assert status == expected and result["success"] is False and result["error"]
    assert {t: client.rows(t) for t in before} == before
    assert client.excel.read_bytes() == excel_before
    assert not (client.base / ".excel_pending_queue.json").exists()
    assert not (client.base / "data_requests.json").exists()


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_http_foreign_origin_cannot_change_an_existing_ledger(live_ledger, method):
    client = live_ledger
    _, item = _create(client)
    exported = client.exported()
    tables = ("request_ledger", "ledger_history", "ledger_outbox", "dataset_revision")
    before = {table: client.rows(table) for table in tables}
    workbook = client.excel.read_bytes()
    status, result = client.request(
        method,
        f"/api/ledger/{item['ledger_id']}",
        {"note": "unauthorized"},
        headers={"Origin": "null"},
    )
    assert status == 403 and not result["success"]
    assert {table: client.rows(table) for table in tables} == before
    assert client.excel.read_bytes() == workbook
    assert client.exported() == exported


def test_http_queued_edit_restart_preserves_manual_excel_change(
    live_ledger, monkeypatch
):
    client = live_ledger
    _, item = _create(client)
    with monkeypatch.context() as failure:
        failure.setattr(
            client.sync,
            "_save_workbook",
            lambda *a: (_ for _ in ()).throw(OSError("disk unavailable")),
        )
        status, edited = client.request(
            "PUT",
            f"/api/ledger/{item['ledger_id']}",
            {"title": "보류된 웹 제목", "expected_updated_at": item["updated_at"]},
        )
    assert (
        status == 200
        and edited["success"]
        and edited["excel_pending"]
        and not edited["excel_synced"]
    )
    assert client.rows("request_ledger")[0]["title"] == "보류된 웹 제목"
    assert client.workbook_rows()[0][4] == item["title"]
    # While the service is stopped, Excel's unrelated cell changes independently.
    book = openpyxl.load_workbook(client.excel)
    book["2026"].cell(2, 12, "엑셀 수기 비고")
    book.save(client.excel)
    book.close()
    restarted = ExcelSyncService(base_dir=client.base)
    monkeypatch.setattr(ExcelSyncService, "_instance", restarted)
    assert restarted.pending_count() == 1
    restarted.check_and_sync_startup()
    assert restarted.pending_count() == restarted.failed_count() == 0
    assert restarted.flush_pending_queue() == 0
    row = client.rows("request_ledger")[0]
    assert (row["title"], row["note"]) == ("보류된 웹 제목", "엑셀 수기 비고")
    assert len(client.workbook_rows()) == 1
    assert (client.workbook_rows()[0][4], client.workbook_rows()[0][11]) == (
        row["title"],
        row["note"],
    )
    exported = client.exported()["request_ledger"][0]
    assert (exported["title"], exported["note"]) == (row["title"], row["note"])


def test_http_search_and_detail_preserve_body_beyond_excel_limit(live_ledger):
    client = live_ledger
    body = "문서 전문 " * 8000 + "문서꼬리표"
    answer = "답변 전문 " * 8000 + "답변꼬리표"
    with closing(sqlite3.connect(client.base / "data_requests.db")) as conn:
        conn.execute(
            "INSERT INTO documents(doc_id, title, full_markdown) VALUES (?,?,?)",
            ("DOC-TAIL", "문서", body),
        )
        conn.execute(
            "INSERT INTO qa_items(qa_id, doc_id, question_title, answer_markdown) VALUES (?,?,?,?)",
            ("QA-TAIL", "DOC-TAIL", "질문", answer),
        )
        conn.commit()
    for query, category, key, id_field, expected in [
        ("문서꼬리표", "docs", "documents", "doc_id", "DOC-TAIL"),
        ("답변꼬리표", "qa", "qa_items", "qa_id", "QA-TAIL"),
    ]:
        status, result = client.request(
            "GET", "/api/search?" + urlencode({"q": query, "category": category})
        )
        assert status == 200 and result["success"], result
        assert [row[id_field] for row in result[key]] == [expected]
    status, detail = client.request("GET", "/api/documents/DOC-TAIL")
    assert status == 200 and detail["success"], detail
    assert detail["data"]["full_markdown"] == body
    assert detail["data"]["qa_items"][0]["answer_markdown"] == answer


@pytest.mark.parametrize("action", ["insert", "update", "delete", "restore"])
def test_http_wal_delivery_matches_actual_workbook(live_ledger, action):
    """A GUI-like open connection must not hide committed Excel delivery intent."""
    client = live_ledger
    _, item = _create(client)
    path = f"/api/ledger/{item['ledger_id']}"
    if action == "restore":
        assert client.request("DELETE", path)[1]["excel_synced"]
    client.exported()
    with closing(sqlite3.connect(client.base / "data_requests.db")) as keeper:
        assert keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        if action == "insert":
            status, result = client.request(
                "POST",
                "/api/ledger",
                {
                    "year": "2026",
                    "requester": "다른 의원실",
                    "title": "WAL 신규 등록",
                    "idempotency_key": "wal-new",
                },
            )
        elif action == "update":
            status, result = client.request("PUT", path, {"title": "WAL 웹 수정"})
        elif action == "delete":
            status, result = client.request("DELETE", path)
        else:
            status, result = client.request("POST", path + "/restore")
        assert status == 200 and result["success"] and result["excel_synced"], result
        assert result["sync_state"] == "applied" and not result["excel_pending"]
        rows = client.workbook_rows()
        if action == "insert":
            assert len(rows) == 2 and rows[1][4] == "WAL 신규 등록"
        elif action == "update":
            assert rows[0][4] == "WAL 웹 수정"
        elif action == "delete":
            assert rows[0][10] == "[삭제]"
        else:
            assert rows[0][10] == item["status"]
        assert client.rows("ledger_outbox") == []
        assert client.sync.pending_count() == 0
        assert client.sync.sync_excel_to_db(write_json=False)["success"]
        expected = "WAL 웹 수정" if action == "update" else item["title"]
        assert client.rows("request_ledger")[0]["title"] == expected


def test_http_wal_queue_failure_restart_protects_web_edit(live_ledger, monkeypatch):
    client = live_ledger
    _, item = _create(client)
    client.exported()
    with closing(sqlite3.connect(client.base / "data_requests.db")) as keeper:
        assert keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        with monkeypatch.context() as failure:
            failure.setattr(client.sync, "_save_pending_queue", lambda: False)
            status, result = client.request(
                "PUT",
                f"/api/ledger/{item['ledger_id']}",
                {"title": "WAL에서 보호할 제목"},
            )
        assert status == 200 and result["success"] and result["excel_pending"]
        assert not result["excel_synced"] and result["sync_state"] == "pending"
        assert len(client.rows("ledger_outbox")) == 1
        restarted = ExcelSyncService(base_dir=client.base)
        monkeypatch.setattr(ExcelSyncService, "_instance", restarted)
        assert restarted.pending_count() == 1
        assert restarted.protected_field_map()[item["ledger_id"]] == {"title"}
        book = openpyxl.load_workbook(client.excel)
        book["2026"].cell(2, 12, "엑셀 수기 비고")
        book.save(client.excel)
        book.close()
        assert restarted.sync_excel_to_db(write_json=False)["success"]
        row = client.rows("request_ledger")[0]
        assert (row["title"], row["note"]) == ("WAL에서 보호할 제목", "엑셀 수기 비고")
        assert restarted.flush_pending_queue() == 1
        assert client.workbook_rows()[0][4] == row["title"]
        assert client.workbook_rows()[0][11] == row["note"]
        assert client.rows("ledger_outbox") == [] and restarted.pending_count() == 0


@pytest.mark.parametrize("exit_code", [0, 7])
def test_http_pipeline_reports_real_process_completion_and_rejects_overlap(
    live_ledger, monkeypatch, exit_code
):
    client = live_ledger
    scripts = client.base / "scripts"
    scripts.mkdir()
    # A real child process waits at an explicit rendezvous. No timing assumption
    # about how long a pipeline normally takes, and no operational script runs.
    (scripts / "add_documents_smart.py").write_text(
        "from pathlib import Path\nimport sys, time\n"
        "base = Path(__file__).resolve().parent\n"
        "(base / 'started').write_text('ready')\n"
        "deadline = time.monotonic() + 15\n"
        "while not (base / 'release').exists():\n"
        "    if time.monotonic() > deadline: sys.exit(99)\n"
        "    time.sleep(0.01)\n"
        "print('synthetic failure', file=sys.stderr)\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    service = SyncService(scripts_dir=scripts, timeout_seconds=20)
    monkeypatch.setattr(web_server, "_sync_service", service)
    try:
        status, started = client.request("POST", "/api/sync")
        assert status == 200 and started["success"]
        deadline = time.monotonic() + 10
        while not (scripts / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (scripts / "started").exists(), service.get_status()
        status, state = client.request("GET", "/api/sync")
        assert status == 200 and state["is_syncing"] and state["last_success"] is None
        status, refused = client.request("POST", "/api/sync")
        assert status == 409 and refused["success"] is False
    finally:
        (scripts / "release").write_text("go", encoding="utf-8")
        deadline = time.monotonic() + 10
        while service.get_status()["is_syncing"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service.get_status()["is_syncing"], "pipeline worker did not stop"
    status, finished = client.request("GET", "/api/sync")
    assert status == 200 and finished["last_success"] is (exit_code == 0)
    assert finished["last_finish_time"] and finished["last_start_time"]
    if exit_code:
        assert (
            str(exit_code) in finished["last_error"]
            and "synthetic failure" in finished["last_error"]
        )
    else:
        assert finished["last_error"] is None
