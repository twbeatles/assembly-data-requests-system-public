"""2026-10-20: WAL delivery visibility and XLSX cell-value preservation."""

import re
import sqlite3
import subprocess
from contextlib import closing

import openpyxl
import pytest
from db.ledger_outbox import acknowledge, enqueue, ensure_tables, read_operations
from pipeline.parse.xlsx_fallback import handle_large_xlsx_fallback
from services.excel_sync_service import ExcelSyncService


def test_outbox_reads_committed_wal_and_acknowledgement(tmp_path):
    db = tmp_path / "live.db"
    with closing(sqlite3.connect(db)) as keeper:
        keeper.execute("PRAGMA journal_mode=WAL")
        ensure_tables(keeper)
        keeper.commit()
        keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        operation = enqueue(keeper, {"ledger_id": "REQ-2026-001"}, "insert")
        keeper.commit()
        # The operation exists only in WAL; no last-connection checkpoint occurs.
        assert [op["operation_id"] for op in read_operations(db)] == [
            operation["_operation_id"]
        ]
        acknowledge(db, [operation["_operation_id"]])
        assert read_operations(db) == []


def test_unknown_delivery_id_is_not_reported_as_applied(tmp_path):
    sync = ExcelSyncService(base_dir=tmp_path)
    result = sync.sync_item_to_excel({"_operation_id": "unknown"}, "update")
    assert not result["excel_synced"] and not result["pending"]
    assert "확인하지 못했습니다" in result["message"]


@pytest.mark.parametrize("converter", ["success_large", "nonzero", "exception"])
def test_xlsx_parse_preserves_falsy_cells_and_intentional_limits(
    tmp_path, monkeypatch, converter
):
    import parse_all

    source = tmp_path / "2026" / "values.xlsx"
    source.parent.mkdir()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append([None, "", 0, 0.0, False, True, "0", -1, "<svg>"])
    sheet["AE1"] = "EXCLUDED_COLUMN_31"
    sheet["A500"] = "INCLUDED_ROW_500"
    sheet["A501"] = "EXCLUDED_ROW_501"
    book.save(source)
    book.close()
    monkeypatch.setattr(parse_all, "ROOT_DIR", tmp_path)
    output_dir = tmp_path / "_parsed_markdown"
    output, _ = parse_all.parse_output_paths(source, output_dir)

    def convert(*args, **kwargs):
        if converter == "exception":
            raise subprocess.TimeoutExpired(args[0], 1)
        if converter == "success_large":
            output.write_text("x" * (501 * 1024), encoding="utf-8")
        return subprocess.CompletedProcess(
            args[0], 0 if converter == "success_large" else 1, "", "failed"
        )

    monkeypatch.setattr(parse_all, "run_converter", convert)
    result = parse_all.parse_single_file(source, output_dir)
    assert result["success"], result
    body = output.read_text(encoding="utf-8")
    first_row = re.search(r"<tr>(.*?)</tr>", body).group(1)
    cells = re.findall(r"<td>(.*?)</td>", first_row)
    assert cells[:9] == ["", "", "0", "0", "False", "True", "0", "-1", "&lt;svg&gt;"]
    assert "INCLUDED_ROW_500" in body
    assert "EXCLUDED_ROW_501" not in body and "EXCLUDED_COLUMN_31" not in body
    assert parse_all.is_parse_cache_fresh(source, output_dir)


def test_xlsx_failed_output_replacement_preserves_previous_body(tmp_path, monkeypatch):
    from pipeline.parse import layout

    source = tmp_path / "values.xlsx"
    book = openpyxl.Workbook()
    book.active.append([0, False])
    book.save(source)
    book.close()
    output = tmp_path / "out.md"
    output.write_text("previous valid conversion", encoding="utf-8")

    def failed_replace(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(layout.os, "replace", failed_replace)
    ok, error = handle_large_xlsx_fallback(source, output)
    assert not ok and "synthetic disk failure" in error
    assert output.read_text(encoding="utf-8") == "previous valid conversion"
    assert not list(tmp_path.glob("*.tmp"))
    # The read-only workbook handle was closed even though output failed.
    source.rename(tmp_path / "closed.xlsx")
