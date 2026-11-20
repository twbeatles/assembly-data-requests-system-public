"""2026-10-21 감사 후속: 프로세스 간 보류 큐, 변환기 timeout, 부작용 GET, 배포 미러링, 입력 검증."""

import io
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock

import openpyxl
import pytest
from services.excel_sync_service import ExcelSyncService

LEDGER_HEADER = ["연번", "의원", "요구자료", "진행상태", "비고", "대장ID"]


def _master(tmp_path, rows, sheets=("2026",), header=LEDGER_HEADER):
    path = tmp_path / "국회요구자료목록.xlsx"
    book = openpyxl.Workbook()
    for name in sheets:
        book.create_sheet(name).append(list(header))
    del book["Sheet"]
    for sheet, row in rows:
        book[sheet].append(list(row))
    book.save(path)
    book.close()
    return path


def _cell(path, sheet, ref):
    book = openpyxl.load_workbook(path)
    try:
        return book[sheet].cell(int(ref[1:]), ord(ref[0]) - ord("A") + 1).value
    finally:
        book.close()


ITEM = {
    "ledger_id": "REQ-2026-001",
    "year": "2026",
    "seq_no": "1",
    "requester": "홍길동",
    "title": "예산 자료",
    "status": "작성중",
    "note": "원래 비고",
    "_expected": {
        "title": "예산 자료",
        "requester": "홍길동",
        "seq_no": "1",
        "aide": "",
    },
}


# --- ISSUE-001: 다른 프로세스가 반영한 보류 작업을 다시 재생하지 않는다 ---------------


def test_queue_applied_by_another_process_is_not_replayed(tmp_path):
    xl = _master(
        tmp_path,
        [("2026", ["1", "홍길동", "예산 자료", "작성중", "원래 비고", "REQ-2026-001"])],
    )
    with mock.patch.object(ExcelSyncService, "find_master_excel", lambda self: xl):
        server = ExcelSyncService(base_dir=tmp_path)
        with mock.patch.object(
            ExcelSyncService, "is_file_locked", lambda self, p: True
        ):
            assert server.sync_item_to_excel(dict(ITEM), action="delete")["pending"]
        pipeline = ExcelSyncService(base_dir=tmp_path)  # 00번·새 문서 반영 프로세스
        assert pipeline.flush_pending_queue() == 1

        assert server.protected_field_map() == {}
        assert server.pending_count() == 0
        assert server.flush_pending_queue() == 0
    assert _cell(xl, "2026", "E2") == "원래 비고 (웹 삭제됨)"
    assert json.loads((tmp_path / ".excel_pending_queue.json").read_text("utf-8")) == []


def test_stale_queue_does_not_overwrite_later_manual_excel_edit(tmp_path):
    xl = _master(
        tmp_path,
        [("2026", ["1", "홍길동", "예산 자료", "작성중", "원래 비고", "REQ-2026-001"])],
    )
    update = dict(ITEM, note="웹 비고", _changed_fields=["note"])
    with mock.patch.object(ExcelSyncService, "find_master_excel", lambda self: xl):
        server = ExcelSyncService(base_dir=tmp_path)
        with mock.patch.object(
            ExcelSyncService, "is_file_locked", lambda self, p: True
        ):
            server.sync_item_to_excel(update, action="update")
        ExcelSyncService(base_dir=tmp_path).flush_pending_queue()
        book = openpyxl.load_workbook(xl)
        book["2026"]["E2"] = "나중의 수기 비고"
        book.save(xl)
        book.close()
        server.flush_pending_queue()
    assert _cell(xl, "2026", "E2") == "나중의 수기 비고"


def test_unpersisted_local_operation_survives_queue_reload(tmp_path):
    xl = _master(
        tmp_path,
        [("2026", ["1", "홍길동", "예산 자료", "작성중", "원래 비고", "REQ-2026-001"])],
    )
    with mock.patch.object(ExcelSyncService, "find_master_excel", lambda self: xl):
        server = ExcelSyncService(base_dir=tmp_path)
        with (
            mock.patch.object(ExcelSyncService, "is_file_locked", lambda self, p: True),
            mock.patch.object(
                ExcelSyncService, "_save_pending_queue", lambda self: False
            ),
        ):
            server.sync_item_to_excel(
                dict(ITEM, _changed_fields=["note"], note="웹 비고"), action="update"
            )
        # 다른 프로세스가 큐 파일을 바꿨다.
        (tmp_path / ".excel_pending_queue.json").write_text("[]", encoding="utf-8")
        assert server.protected_field_map() == {"REQ-2026-001": {"note"}}


# --- ISSUE-002: 변환기 프로세스 트리를 timeout에 끝낸다 ------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe 래퍼 경로는 Windows 전용")
def test_converter_timeout_kills_process_tree(tmp_path, monkeypatch):
    import parse_all
    from pipeline_guard import pid_is_alive

    pid_file = tmp_path / "child.pid"
    fake = tmp_path / "fake_kordoc.cmd"
    code = (
        f"import os,time;open(r'{pid_file}','w').write(str(os.getpid()));time.sleep(30)"
    )
    fake.write_text(f'@"{sys.executable}" -c "{code}"\r\n', encoding="utf-8")
    source = tmp_path / "2026" / "doc.hwp"
    source.parent.mkdir()
    source.write_bytes(b"hwp")
    output = tmp_path / "_parsed_markdown"
    monkeypatch.setattr(parse_all, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(parse_all, "KORDOC_CMD", str(fake))
    monkeypatch.setattr(parse_all, "parse_timeout_seconds", lambda path: 3)
    body, cache = parse_all.parse_output_paths(source, output)
    body.parent.mkdir(parents=True)
    body.write_text("last valid body", encoding="utf-8")
    cache.write_text("old hash", encoding="utf-8")

    started = time.monotonic()
    result = parse_all.parse_single_file(source, output, force=True)
    elapsed = time.monotonic() - started

    assert not result["success"]
    assert elapsed < 15, elapsed
    assert pid_file.exists()
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while pid_is_alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_is_alive(child)
    assert body.read_text(encoding="utf-8") == "last valid body"
    assert cache.read_text(encoding="utf-8") == "old hash"


# --- ISSUE-003·Gap: 부작용 있는 요청의 출처 검사 ------------------------------------


def _handler(path, method="GET", body=None, headers=None):
    import web_server

    handler: Any = web_server.RequestLedgerHandler.__new__(
        web_server.RequestLedgerHandler
    )
    handler.path = path
    handler.command = method
    handler.headers = {"Host": "127.0.0.1:8080", **(headers or {})}
    handler.server = mock.Mock(server_address=("127.0.0.1", 8080))
    handler.wfile = io.BytesIO()
    handler.sent_status = None
    handler.sent_json = None

    def send_json(data, status_code=200):
        handler.sent_status = status_code
        handler.sent_json = data

    handler.send_json_response = send_json
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    handler.rfile = io.BytesIO(raw)
    if body is not None:
        handler.headers["Content-Length"] = str(len(raw))
    return handler


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import web_server

    (tmp_path / "2026").mkdir()
    doc = tmp_path / "2026" / "문서.hwp"
    doc.write_bytes(b"hwp")
    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setattr(web_server, "BASE_DIR", system)
    return doc


def test_get_open_is_not_allowed(workspace):
    handler = _handler(
        "/api/open?path=2026/%EB%AC%B8%EC%84%9C.hwp&mode=file",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    with (
        mock.patch("web.downloads.open_in_system") as opener,
        mock.patch("subprocess.Popen"),
        mock.patch("os.startfile", create=True),
    ):
        handler.do_GET()
    assert handler.sent_status == 405
    opener.assert_not_called()


def test_cross_site_post_open_is_rejected(workspace):
    handler = _handler(
        "/api/open",
        "POST",
        {"path": "2026/문서.hwp", "mode": "explorer"},
        headers={"Origin": "http://evil.example"},
    )
    with mock.patch("web.downloads.open_in_system") as opener:
        handler.do_POST()
    assert handler.sent_status == 403
    opener.assert_not_called()


def test_post_open_rejects_unknown_mode(workspace):
    handler = _handler("/api/open", "POST", {"path": "2026/문서.hwp", "mode": "run"})
    with mock.patch("web.downloads.open_in_system") as opener:
        handler.do_POST()
    assert handler.sent_status == 400
    opener.assert_not_called()


def test_cross_site_db_health_is_rejected(workspace):
    handler = _handler("/api/db/health", headers={"Sec-Fetch-Site": "cross-site"})
    with mock.patch("db.health.check_database") as check:
        handler.do_GET()
    assert handler.sent_status == 403
    check.assert_not_called()


def test_dashboard_opens_file_location_with_post():
    js = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "templates"
        / "dashboard"
        / "js"
        / "03_ui.js"
    ).read_text(encoding="utf-8")
    assert "fetch('/api/open?" not in js
    assert (
        "method: 'POST'"
        in js.split("async function openFileLocation", 1)[1].split("\nfunction ", 1)[0]
    )


# --- ISSUE-004: 배포 마크다운 미러링 ---------------------------------------------


def test_distribution_markdown_mirror_removes_deleted_sources(tmp_path):
    from distribution.copy import mirror_markdown_tree

    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "2026").mkdir(parents=True)
    (src / "2026" / "a.hwp.md").write_text("a", encoding="utf-8")
    (src / "2026" / "a.hwp.md.sha256").write_text("h", encoding="utf-8")
    (src / "2026" / "b.hwp.md").write_text("b", encoding="utf-8")
    (src / "2026" / "c.hwp.md.previous").write_text("transient", encoding="utf-8")
    mirror_markdown_tree(src, dst)
    (src / "2026" / "b.hwp.md").unlink()
    (src / "2026" / "a.hwp.md").write_text("a2", encoding="utf-8")
    mirror_markdown_tree(src, dst)

    files = sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())
    assert files == ["2026/a.hwp.md", "2026/a.hwp.md.sha256"]
    assert (dst / "2026" / "a.hwp.md").read_text(encoding="utf-8") == "a2"


def test_package_distribution_uses_markdown_mirror():
    import package_distribution
    from distribution import copy as dist_copy

    assert package_distribution.mirror_markdown_tree is dist_copy.mirror_markdown_tree


# --- Gap: 대장 입력 검증 --------------------------------------------------------


@pytest.fixture
def ledger(tmp_path):
    from services.ledger_service import LedgerService

    service = LedgerService(base_dir=tmp_path)
    service._ensure_schema()
    with sqlite3.connect(service.db_path) as conn:
        conn.execute(
            "INSERT INTO documents (doc_id, year, title) VALUES ('DOC-2026-001', '2026', '답변')"
        )
    return service


def test_insert_rejects_missing_linked_document(ledger):
    result = ledger.insert_ledger_item(
        {"title": "t", "requester": "r", "linked_doc_id": "DOC-없음"}
    )
    assert not result["success"] and result.get("code") == "invalid"
    with sqlite3.connect(ledger.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM request_ledger").fetchone()[0] == 0


def test_insert_with_linked_document_writes_back_reference(ledger):
    result = ledger.insert_ledger_item(
        {"title": "t", "requester": "r", "linked_doc_id": "DOC-2026-001"}
    )
    assert result["success"], result
    with sqlite3.connect(ledger.db_path) as conn:
        back = conn.execute(
            "SELECT linked_ledger_id FROM documents WHERE doc_id='DOC-2026-001'"
        ).fetchone()[0]
    assert back == result["item"]["ledger_id"]


@pytest.mark.parametrize("status", ["[삭제]", " [삭제] "])
def test_tombstone_status_cannot_be_set_through_insert_or_update(ledger, status):
    blocked = ledger.insert_ledger_item(
        {"title": "t", "requester": "r", "status": status}
    )
    assert not blocked["success"] and blocked.get("code") == "invalid"
    created = ledger.insert_ledger_item({"title": "t", "requester": "r"})
    lid = created["item"]["ledger_id"]
    updated = ledger.update_ledger_item(lid, {"status": status})
    assert not updated["success"] and updated.get("code") == "invalid"
    assert ledger.get_ledger_item(lid)["status"] == "작성중"


@pytest.mark.parametrize("value", [{"x": 1}, ["a"], 3, None, "가" * 201])
def test_config_api_rejects_non_text_values(workspace, value):
    import system_config

    handler = _handler("/api/config", "POST", {"department_name": value})
    with mock.patch.object(system_config, "save_config") as save:
        handler.do_POST()
    assert handler.sent_status == 400
    save.assert_not_called()


def test_storage_busy_is_reported_separately_from_pipeline(tmp_path):
    import web_server
    from storage_activity import StorageBusyError, guarded_write

    class Busy:
        base_dir = tmp_path

        @guarded_write()
        def write(self):
            raise StorageBusyError("다른 저장 또는 문서 갱신이 진행 중입니다.")

    result = Busy().write()
    assert result["code"] == "storage_busy"
    assert web_server.status_for_result(result) == 409


# --- Gap: 연도 이동은 원래 행의 엑셀 값을 보존한다 -----------------------------------


def test_year_move_keeps_unmapped_columns_and_unchanged_manual_edits(tmp_path):
    header = LEDGER_HEADER[:5] + ["담당자메모", "대장ID"]
    xl = _master(
        tmp_path,
        [
            (
                "2025",
                [
                    "1",
                    "홍길동",
                    "예산 자료",
                    "작성중",
                    "엑셀 수기 비고",
                    "메모X",
                    "REQ-2025-001",
                ],
            )
        ],
        sheets=("2025", "2026"),
        header=header,
    )
    item = dict(
        ITEM,
        ledger_id="REQ-2025-001",
        year="2026",
        note="옛 비고",
        _changed_fields=["year"],
    )
    with (
        mock.patch.object(ExcelSyncService, "find_master_excel", lambda self: xl),
        mock.patch.object(ExcelSyncService, "is_file_locked", lambda self, p: False),
    ):
        assert ExcelSyncService(base_dir=tmp_path).sync_item_to_excel(
            item, action="update"
        )["excel_synced"]
    book = openpyxl.load_workbook(xl)
    try:
        assert book["2025"].max_row == 1
        row = [c.value for c in book["2026"][2]]
    finally:
        book.close()
    assert row == [
        "1",
        "홍길동",
        "예산 자료",
        "작성중",
        "엑셀 수기 비고",
        "메모X",
        "REQ-2025-001",
    ]
