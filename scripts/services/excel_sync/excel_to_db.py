# -*- coding: utf-8 -*-
import sys
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
except ImportError:
    openpyxl = None


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.excel_sync.service import ExcelSyncService

    _HostBase_ExcelToDbMixin = ExcelSyncService
else:
    _HostBase_ExcelToDbMixin = object


class ExcelToDbMixin(_HostBase_ExcelToDbMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def sync_excel_to_db(self, write_json: bool = True) -> Dict[str, Any]:
        """
        Scans the master Excel file and synchronizes any manual additions or updates into the SQLite DB.

        `write_json=False`: JSON 캐시를 곧바로 다시 만드는 호출자(파이프라인)용.
        """
        master_excel = self.find_master_excel()
        if not master_excel or not master_excel.exists():
            return self._remember_excel_sync(
                {"success": False, "reason": "no_master_excel", "message": "마스터 엑셀 파일을 찾을 수 없습니다."}
            )

        blocked = self._pipeline_block()
        if blocked:
            print(f"ℹ️ [엑셀 동기화 보류] {blocked['message']}")
            return blocked

        with self.lock:
            # 행 위치 기반 ID가 밀리는 것을 막기 위해 파싱 전에 ID를 고정한다.
            # 스탬핑은 워크북을 통째로 다시 쓰므로 반드시 락 안에서 한다.
            self._ensure_ledger_ids(master_excel)

            from extractors.ledger_parser import load_request_ledger
            from services.ledger_service import LedgerService

            # Read documents from DB to support document auto-linking
            service = LedgerService(db_path=self.db_path, json_path=self.json_path, base_dir=self.base_dir)
            conn = service.get_conn()
            try:
                cur = conn.cursor()
                docs = [dict(r) for r in cur.execute("SELECT * FROM documents").fetchall()]
                # 엑셀을 읽기 **전**의 대장 ID 집합. 삭제 판정은 이 집합에 있던 행만
                # 대상으로 한다. 파싱 도중 웹에서 새로 등록된 행은 "엑셀에 없다"가
                # 아니라 "아직 쓰이지 않았다"이므로 판정할 근거가 없다.
                # 이 방어선이 없으면 동기화 중에 누른 등록이 성공 메시지와 함께
                # 곧바로 [삭제] 처리된다.
                known_ledger_ids = {
                    r[0] for r in cur.execute("SELECT ledger_id FROM request_ledger").fetchall()
                }
            finally:
                conn.close()

            # Parse Excel only (avoid circular DB re-merge)
            # 스탬핑·쓰기에 쓴 바로 그 파일을 파싱한다. 파서가 따로 탐색하면 다른 파일을 고를 수
            # 있다. (감사 R4-03)
            parsed_items = load_request_ledger(self.base_dir, docs, merge_db_records=False,
                                               db_path=self.db_path, system_dir=self.base_dir,
                                               excel_path=master_excel)

            # 대장 파일은 있는데 한 건도 읽지 못했다. 헤더·시트명 인식 실패나 잘못된 파일 선택의
            # 신호이지 "대장이 비었다"가 아니다(DB에 엑셀 출처 항목이 있기 때문). 예전에는 이것을
            # 성공(0건)으로 보고해 엑셀 수기 수정이 조용히 반영되지 않았다. (감사 R4-07)
            if not parsed_items:
                excel_known = self._excel_backed_active_count()
                if excel_known > 0:
                    now_mtime = master_excel.stat().st_mtime
                    self._last_known_excel_mtime = now_mtime
                    self._external_change_pending = False
                    message = (
                        f"마스터 엑셀({master_excel.name})에서 대장 항목을 한 건도 읽지 못했습니다. "
                        f"DB에는 엑셀 출처 항목 {excel_known}건이 있습니다. 시트 이름(연도)과 1행 헤더를 확인하세요."
                    )
                    print(f"🚨 [엑셀 동기화 중단] {message}")
                    return self._remember_excel_sync({
                        "success": False, "skipped": True, "reason": "parsed_zero",
                        "inserted": 0, "updated": 0, "deleted": 0, "total": 0,
                        "excel": master_excel.name, "message": message,
                    })

            # 보류 큐·격리 파일에 남은 웹 변경은 아직 Excel에 없다. 이 ID들을 Excel 기준으로
            # 덮어쓰거나 삭제 처리하면 사용자가 등록·수정한 내용이 사라진다.
            # 파싱이 끝난 **뒤에** 읽어야 파싱 도중 큐에 들어온 항목도 보호된다.
            protected_fields = self.protected_field_map()
            pending_ids = set(protected_fields.keys())

            # Upsert into SQLite
            conn = service.get_conn()
            cur = conn.cursor()
            inserted = 0
            updated = 0
            deleted = 0

            try:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for it in parsed_items:
                    lid = it.get("ledger_id")
                    if not lid:
                        continue
                    guarded_fields = set()
                    if lid in pending_ids:
                        # 아직 Excel에 반영되지 않은 웹 변경이 큐에 있다. 그 변경이 건드린
                        # 필드는 DB 값을 지키고, 나머지 칸의 엑셀 수기 수정은 받아들인다.
                        if protected_fields.get(lid) is None:
                            continue
                        guarded_fields = protected_fields[lid]

                    cur.execute("SELECT * FROM request_ledger WHERE ledger_id = ?", (lid,))
                    existing = cur.fetchone()

                    # [삭제] is a tombstone written by the web delete path.  It
                    # must never become a new active row on a later Excel scan.
                    if str(it.get("status") or "").strip() == "[삭제]":
                        if guarded_fields:
                            continue
                        if existing and str(existing["status"] or "").strip() != "[삭제]":
                            cur.execute("UPDATE request_ledger SET status = ?, updated_at = ? WHERE ledger_id = ?", ("[삭제]", now_str, lid))
                            cur.execute(
                                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                                (lid, "EXCEL_SYNC_DELETE", now_str, json.dumps(dict(existing), ensure_ascii=False))
                            )
                            deleted += 1
                        continue

                    # A DB tombstone wins over an active-looking Excel row.  The
                    # latter can be a stale workbook snapshot from before a web
                    # deletion; restoration requires an explicit new registration.
                    if existing and str(existing["status"] or "").strip() == "[삭제]":
                        continue

                    if not existing:
                        cur.execute("""
                            INSERT INTO request_ledger (
                                ledger_id, year, seq_no, party, requester, aide,
                                title, details, request_date, deadline, submit_date,
                                department, status, note, request_type, linked_doc_id,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            lid, it.get("year", ""), it.get("seq_no", ""), it.get("party", ""),
                            it.get("requester", ""), it.get("aide", ""), it.get("title", ""),
                            it.get("details", ""), it.get("request_date", ""), it.get("deadline", ""),
                            it.get("submit_date", ""), it.get("department", ""), it.get("status", ""),
                            it.get("note", ""), it.get("request_type", ""), it.get("linked_doc_id", ""),
                            it.get("created_at", now_str), it.get("updated_at", now_str)
                        ))
                        try:
                            cur.execute(
                                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                                (lid, "EXCEL_SYNC_INSERT", now_str, json.dumps(it, ensure_ascii=False))
                            )
                        except Exception:
                            pass
                        inserted += 1
                    else:
                        # Compare fields to check if Excel manual edits need updating (including cleared cells)
                        changed = False
                        update_fields = []
                        params = []
                        # `year`도 비교한다. 행을 다른 연도 시트로 옮기면 파싱된 year가
                        # 바뀌는데, 예전에는 비교 목록에 없어 DB의 연도가 영영 어긋났다.
                        for field in ["year", "title", "details", "requester", "party", "aide", "seq_no",
                                      "request_date", "deadline", "submit_date", "department",
                                      "status", "note", "request_type"]:
                            if field in guarded_fields:
                                continue
                            excel_val = str(it.get(field, "") or "").strip()
                            db_val = str(existing[field] or "").strip()
                            if excel_val != db_val:
                                changed = True
                                update_fields.append(f"{field} = ?")
                                params.append(excel_val)

                        if changed:
                            update_fields.append("updated_at = ?")
                            params.append(now_str)
                            params.append(lid)
                            cur.execute(f"UPDATE request_ledger SET {', '.join(update_fields)} WHERE ledger_id = ?", params)
                            try:
                                cur.execute(
                                    "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                                    (lid, "EXCEL_SYNC_UPDATE", now_str, json.dumps(dict(existing), ensure_ascii=False))
                                )
                            except Exception:
                                pass
                            updated += 1

                excel_ids = {it.get("ledger_id") for it in parsed_items if it.get("ledger_id")}
                # 웹에서 등록했지만 아직 한 번도 엑셀에 쓰이지 않은 행. "엑셀에 없다"가 삭제의
                # 근거가 되지 않는다. 예전에는 요청형태가 '시스템'인지로 이것을 가렸는데,
                # 운영 대장에서 '시스템'은 "국회 요구자료 시스템으로 접수"라는 업무 값이라
                # 대장의 과반이 엑셀 행 삭제에 반응하지 않았다. (감사 R3-10)
                web_only_ids = self._web_only_ledger_ids(cur)

                active_rows = cur.execute(
                    "SELECT ledger_id FROM request_ledger WHERE COALESCE(status, '') != '[삭제]'"
                ).fetchall()
                total_existing = sum(1 for r in active_rows if r["ledger_id"] not in web_only_ids)

                # Safety guard: 파싱 건수가 급감하면 일괄 삭제하지 않는다. 대장 규모와 무관하게
                # 비율만으로 판단한다. (도입 초기의 소규모 대장도 보호)
                allow_delete = True
                if total_existing > 0 and len(excel_ids) < total_existing * 0.5:
                    allow_delete = False
                    print(f"⚠️ [엑셀 동기화 안전장치] 파싱 건수({len(excel_ids)}건)가 기존 DB({total_existing}건) 대비 50% 미만이므로 일괄 삭제를 건너뜁니다.")

                if excel_ids and allow_delete:
                    for row in cur.execute("SELECT ledger_id, status FROM request_ledger").fetchall():
                        lid = row["ledger_id"]
                        st = str(row["status"] or "")
                        if lid in pending_ids or lid in web_only_ids:
                            continue
                        # 엑셀을 읽은 뒤에 생긴 행은 판정 대상이 아니다.
                        if lid not in known_ledger_ids:
                            continue
                        if lid not in excel_ids and st != "[삭제]":
                            cur.execute(
                                "UPDATE request_ledger SET status = ?, updated_at = ? WHERE ledger_id = ?",
                                ("[삭제]", now_str, lid)
                            )
                            cur.execute(
                                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                                (lid, "EXCEL_SYNC_DELETE", now_str, json.dumps({"ledger_id": lid}, ensure_ascii=False))
                            )
                            deleted += 1

                conn.commit()
            finally:
                conn.close()

            # Record mtime
            now_mtime = master_excel.stat().st_mtime
            self._last_known_excel_mtime = now_mtime
            self._external_change_pending = False
            changed = inserted > 0 or updated > 0 or deleted > 0

        # JSON 캐시(수십~수백 MB) 재작성은 워크북 락 밖에서 한다. 락 안에서 쓰면 그동안
        # 웹 등록의 엑셀 반영이 막혀 HTTP 응답이 수 초씩 지연된다. (감사 R3-16)
        if changed:
            if write_json:
                service.sync_json_file(async_mode=False)
            self.bump_version()
            print(f"✓ 엑셀 수기 수정 감지 ➔ DB 반영 완료 (신규 {inserted}건, 수정 {updated}건, 삭제 {deleted}건)")

        return self._remember_excel_sync({
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "deleted": deleted,
            "total": len(parsed_items),
            "excel": master_excel.name,
            "message": f"동기화 완료 (신규 {inserted}건, 수정 {updated}건, 삭제 {deleted}건)"
        })

    def _remember_excel_sync(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """마지막 엑셀→DB 동기화 결과를 남긴다. `/api/sync`의 `excel_last_sync`로 보인다."""
        record = {k: result.get(k) for k in ("success", "reason", "inserted", "updated", "deleted",
                                               "total", "excel", "message") if k in result}
        record["at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_excel_sync = record
        return result

    def last_excel_sync(self) -> Optional[Dict[str, Any]]:
        return getattr(self, "_last_excel_sync", None)

    def _excel_backed_active_count(self) -> int:
        """삭제되지 않았고 웹 전용이 아닌(= 엑셀에서 온) 대장 항목 수."""
        if not self.db_path.exists():
            return 0
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_ledger'"
            ).fetchone()
            if not exists:
                return 0
            web_only = self._web_only_ledger_ids(conn.cursor())
            rows = conn.execute(
                "SELECT ledger_id FROM request_ledger WHERE COALESCE(status, '') != '[삭제]'"
            ).fetchall()
            return sum(1 for r in rows if r[0] not in web_only)
        except sqlite3.Error:
            return 0
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _web_only_ledger_ids(cur) -> set:
        """웹에서 등록(INSERT 이력)했지만 엑셀에 쓰였다는 이력이 없는 대장 ID."""
        try:
            rows = cur.execute("""
                SELECT ledger_id,
                       SUM(CASE WHEN action = 'INSERT' THEN 1 ELSE 0 END) AS web_inserts,
                       SUM(CASE WHEN action IN ('EXCEL_APPLIED', 'EXCEL_SYNC_INSERT', 'EXCEL_SYNC_UPDATE')
                                THEN 1 ELSE 0 END) AS excel_seen
                FROM ledger_history
                GROUP BY ledger_id
            """).fetchall()
        except sqlite3.Error:
            return set()
        return {r[0] for r in rows if r[1] and not r[2]}
