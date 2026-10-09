# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import sqlite3
import datetime
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from pipeline_guard import PipelineGuard
from write_guard import ensure_writable, ProductionWriteBlocked

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
except ImportError:
    openpyxl = None

from services.excel_sync.identity import (
    MAX_PENDING_ATTEMPTS,
    QUARANTINE_CONFLICT,
    QUARANTINE_RETRY_LIMIT,
    ExcelApplyConflict,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.excel_sync.service import ExcelSyncService

    _HostBase_PendingQueueMixin = ExcelSyncService
else:
    _HostBase_PendingQueueMixin = object


class PendingQueueMixin(_HostBase_PendingQueueMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def _outbox_operations(self):
        from db.ledger_outbox import read_operations
        return read_operations(self.db_path)

    def _recover_outbox(self):
        # Caller holds _flush_lock; persist JSON before acknowledging SQLite.
        from db.ledger_outbox import acknowledge
        operations = self._outbox_operations()
        if not operations:
            return True
        with self._pending_lock:
            known = {op.get("operation_id") for op in self._pending_queue}
            self._pending_queue.extend(op for op in operations if op["operation_id"] not in known)
            if not self._save_pending_queue():
                return False
            acknowledge(self.db_path, [op["operation_id"] for op in operations])
        return True

    def _sync_durable_operation(self, operation_id):
        self.flush_pending_queue()
        operations = self._outbox_operations()
        with self._pending_lock:
            operations += list(self._pending_queue)
        pending = any(op.get("operation_id") == operation_id for op in operations)
        failed = any(op.get("operation_id") == operation_id for op in self._quarantined_operations())
        if operation_id in self._missing_delete_ids:
            self._missing_delete_ids.discard(operation_id)
            return {"excel_synced": False, "pending": False,
                    "message": "엑셀에서 해당 행을 찾지 못해 DB 삭제만 반영했습니다."}
        return {"excel_synced": not pending and not failed, "pending": pending, "quarantined": failed,
                "message": ("DB 저장 완료. 엑셀 반영을 안전하게 보관했으며 자동 재시도합니다." if pending else
                            "DB 저장 완료. 엑셀 행은 덮어쓰지 않았습니다. 반영 작업이 격리되어 동기화 배너에서 확인이 필요합니다." if failed else "엑셀 반영 완료.")}

    def _pending_path(self) -> Path:
        return self.base_dir / ".excel_pending_queue.json"

    def _load_pending_queue(self):
        p = self._pending_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            self._pending_queue = []
            self._handle_corrupt_queue(p, e)
            return
        if isinstance(data, list):
            self._pending_queue = data
        else:
            self._pending_queue = []
            self._handle_corrupt_queue(p, ValueError("최상위 값이 목록([ ])이 아닙니다"))

    def _handle_corrupt_queue(self, path: Path, error: Exception):
        """깨진 보류 큐를 보존하고 알린다.

        예전에는 조용히 빈 큐로 시작해, 아직 엑셀에 없는 웹 변경의 보호가 풀리고 다음
        엑셀→DB 동기화가 그 변경을 엑셀 옛값으로 되돌렸다. 손상 사실도 어디에도 남지 않았다.
        원본은 다음 큐 저장에 덮어쓰이기 전에 복사해 둔다. (감사 R4-11)
        """
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(f"{path.stem}.corrupt.{stamp}.json")
        saved = None
        try:
            ensure_writable(backup, "손상된 보류 큐 보존본")
            import shutil
            shutil.copy2(str(path), str(backup))
            saved = backup
        except Exception as copy_error:
            print(f"[엑셀 동기화 큐 경고] 손상된 보류 큐를 보존하지 못했습니다: {copy_error}")
        self._queue_corrupt = {
            "path": str(path),
            "backup": str(saved) if saved else None,
            "error": str(error),
            "detected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        print(
            f"🚨 [엑셀 동기화 큐 손상] {path.name}을(를) 읽을 수 없어 빈 큐로 시작합니다: {error}\n"
            f"   원본 보존: {saved.name if saved else '(보존 실패)'}\n"
            "   아직 엑셀에 반영되지 않은 웹 변경이 있었다면 엑셀 동기화에서 보호되지 않습니다.\n"
            "   python scripts/reconcile_storage.py --excel 로 DB와 엑셀 차이를 먼저 확인하세요."
        )

    def queue_health(self) -> Optional[Dict[str, Any]]:
        """보류 큐 손상 정보. 정상이면 None."""
        return getattr(self, "_queue_corrupt", None)

    def _save_pending_queue(self) -> bool:
        """Persist the queue before reporting a deferred write as recoverable."""
        p = self._pending_path()
        tmp = p.with_suffix(".json.tmp")
        try:
            ensure_writable(p, "엑셀 보류 큐")
            tmp.write_text(json.dumps(self._pending_queue, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
            return True
        except Exception as e:
            print(f"[엑셀 동기화 큐 경고] 보류 큐 저장 실패 ({p}): {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _new_operation(item: Dict[str, Any], action: str) -> Dict[str, Any]:
        return {
            "operation_id": uuid.uuid4().hex,
            "action": action,
            "item": dict(item),
            "timestamp": time.time(),
            "attempts": 0,
        }

    def _enqueue_pending(self, item: Dict[str, Any], action: str) -> bool:
        self._pending_queue.append(self._new_operation(item, action))
        return self._save_pending_queue()

    def get_version(self) -> int:
        """Returns the current data version counter for frontend polling."""
        return self._version_counter

    def bump_version(self):
        """Increments version counter to notify frontend of updates."""
        self._version_counter = int(time.time() * 1000)

    # -------------------------------------------------------------------------
    # 0. Shared guards and ledger ID stabilization
    # -------------------------------------------------------------------------
    def _pipeline_block(self) -> Optional[Dict[str, Any]]:
        """파이프라인이 DB를 재구축하는 동안에는 Excel 동기화를 하지 않는다.

        재구축 중 반영한 변경은 shadow DB 교체 때 사라지기 때문이다.
        """
        try:
            guard = PipelineGuard(self.base_dir)
            if guard.is_locked() and not guard.is_mine():
                return {
                    "success": False,
                    "skipped": True,
                    "message": "문서 동기화 파이프라인이 실행 중입니다. 완료 후 자동으로 다시 시도합니다."
                }
        except Exception:
            return None
        return None

    def _pending_ledger_ids(self) -> set:
        """보류 큐에 남아 있는 ledger_id 집합. Excel에 아직 없는 웹 변경을 보호한다."""
        with self._pending_lock:
            ids = {
                str((op.get("item") or {}).get("ledger_id") or "").strip()
                for op in self._pending_queue
            }
        ids.discard("")
        return ids

    def _quarantined_operations(self) -> List[Dict[str, Any]]:
        path = self._failed_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def protected_ledger_ids(self) -> set:
        """엑셀 기준 덮어쓰기·삭제에서 제외할 ID.

        보류 큐에 남은 작업뿐 아니라, 재시도 한도를 넘겨 격리된 작업도 DB에만 반영된
        웹 변경이다. 큐에서 빠졌다고 보호를 풀면 다음 엑셀→DB 동기화가 웹 수정을 엑셀의
        옛 값으로 되돌린다. (감사 R3-07) 단, 충돌로 격리된 작업은 엑셀 행과 맞지 않아
        거부된 것이라 보호하지 않는다.
        """
        return set(self.protected_field_map().keys())

    def protected_field_map(self) -> Dict[str, Optional[set]]:
        """보호할 대장 ID → 보호할 필드 집합(None이면 행 전체).

        수정 작업은 실제로 바꾼 필드(`_changed_fields`)만 보호한다. 행 전체를 보호하면
        웹 수정이 보류된 동안 사용자가 엑셀에서 **다른 칸**을 고친 내용까지 무시된다.
        등록·삭제·필드 정보가 없는 과거 작업은 행 전체를 보호한다.
        """
        with self._pending_lock:
            operations = list(self._pending_queue)
        operations += self._outbox_operations()
        operations += [
            op for op in self._quarantined_operations()
            if op.get("quarantine_reason") != QUARANTINE_CONFLICT
        ]
        result: Dict[str, Optional[set]] = {}
        for op in operations:
            item = op.get("item") or {}
            lid = str(item.get("ledger_id") or "").strip()
            if not lid:
                continue
            changed = item.get("_changed_fields")
            fields = set(changed) if op.get("action") == "update" and isinstance(changed, list) else None
            if lid in result and (result[lid] is None or fields is None):
                result[lid] = None
            elif lid in result:
                prev = result[lid]
                assert prev is not None and fields is not None
                prev |= fields
            else:
                result[lid] = fields
        return result

    def pending_count(self) -> int:
        """보류 큐에 남은 엑셀 반영 작업 수. 상태 API/UI 노출용."""
        with self._pending_lock:
            ids = {op.get("operation_id") for op in self._pending_queue}
        ids.update(op.get("operation_id") for op in self._outbox_operations())
        return len(ids)

    def ensure_ledger_ids(self) -> int:
        """마스터 엑셀을 찾아 `대장ID`를 고정한다. 파이프라인 등 외부 호출용."""
        master_excel = self.find_master_excel()
        if not master_excel or not master_excel.exists():
            return 0
        if self.is_file_locked(master_excel):
            return 0
        return self._ensure_ledger_ids(master_excel)

    def _ledger_db_records(self) -> List[Dict[str, Any]]:
        """ID 스탬핑 판단에 쓸 DB 대장 레코드(ledger_id/year/title/requester)."""
        if not self.db_path.exists():
            return []
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_ledger'"
            ).fetchone()
            if not exists:
                return []
            return [
                dict(r) for r in conn.execute(
                    "SELECT ledger_id, year, title, requester, status FROM request_ledger"
                )
            ]
        except sqlite3.Error:
            return []
        finally:
            if conn:
                conn.close()

    def _ensure_ledger_ids(self, master_excel: Optional[Path]) -> int:
        """마스터 엑셀의 빈 `대장ID` 칸을 채워 행 위치 변화에 안전하게 만든다.

        ID가 없으면 파서가 행 순서로 ID를 매기므로, 행 삭제·삽입만으로 서로
        다른 항목의 ID가 뒤바뀐다. 잠금·권한 문제로 기록하지 못하면 조용히
        건너뛰고 다음 기회에 다시 시도한다.
        """
        if openpyxl is None or not master_excel or not master_excel.exists():
            return 0
        try:
            stat = master_excel.stat()
            fingerprint = (str(master_excel.resolve()), stat.st_mtime, stat.st_size)
        except OSError:
            return 0
        if self._id_stamp_fingerprint == fingerprint:
            return 0

        from extractors.ledger_parser import stamp_ledger_ids
        try:
            written = stamp_ledger_ids(master_excel, self._ledger_db_records(), base_dir=self.base_dir)
        except PermissionError:
            print("[관리대장 ID 고정 보류] 엑셀 파일이 사용 중이라 ID 스탬핑을 건너뜁니다.")
            return 0
        except Exception as e:
            print(f"[관리대장 ID 고정 알림] {e}")
            return 0

        try:
            before_mtime = fingerprint[1]
            stat = master_excel.stat()
            self._id_stamp_fingerprint = (str(master_excel.resolve()), stat.st_mtime, stat.st_size)
            if written:
                # 우리가 기록한 변경을 watcher가 외부 편집으로 오인하지 않게 한다.
                self._mark_own_write(before_mtime, stat.st_mtime)
        except OSError:
            self._id_stamp_fingerprint = None
        return written

    def _mark_own_write(self, before_mtime: float, after_mtime: float):
        """이 서비스가 엑셀을 저장했음을 기록한다.

        저장 직전 파일이 watcher가 아직 읽지 않은 사용자 저장본이었다면, mtime을 우리 저장
        시각으로 덮어 그 편집을 영영 놓치지 않도록 "외부 변경 대기"로 표시해 둔다.
        """
        if before_mtime > self._last_known_excel_mtime:
            self._external_change_pending = True
        else:
            self._last_known_excel_mtime = after_mtime
        self._last_web_write_time = after_mtime

    def _record_history(self, ledger_id: str, action: str, snapshot: Dict[str, Any]):
        """엑셀 반영 결과를 ledger_history에 남긴다. 실패해도 동기화를 막지 않는다."""
        if not ledger_id or not self.db_path.exists():
            return
        conn = None
        try:
            ensure_writable(self.db_path, "SQLite DB")
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            clean = {k: v for k, v in (snapshot or {}).items() if not str(k).startswith("_")}
            conn.execute(
                "INSERT INTO ledger_history (ledger_id, action, changed_at, snapshot) VALUES (?, ?, ?, ?)",
                (ledger_id, action, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 json.dumps(clean, ensure_ascii=False)),
            )
            conn.commit()
        except (sqlite3.Error, ProductionWriteBlocked):
            pass
        finally:
            if conn:
                conn.close()
    def flush_pending_queue(self) -> int:
        with self._flush_lock:
            if self._pipeline_block():
                return 0
            if not self._recover_outbox():
                return 0
            return self._flush_pending_queue_locked()

    def _flush_pending_queue_locked(self) -> int:
        """Flushes any pending operations waiting on an Excel file lock.

        락 규칙: `_pending_lock`은 큐 자료구조만 보호하고, 워크북 쓰기는 `self.lock`
        아래에서 한다. 두 락을 함께 쥘 때는 **항상 `self.lock` → `_pending_lock` 순서**다
        (`sync_item_to_excel`의 큐 적재, `sync_excel_to_db`의 보호 필드 조회). 이 함수는
        `_pending_lock`을 놓은 뒤에 `self.lock`을 잡는다. `_pending_lock`을 쥔 채 `self.lock`을
        기다리는 역순은 금지다. 교착이 생긴다. (감사 R4-10)
        예전에는 flush가 `_pending_lock`만 쥔 채 워크북을 써서, `self.lock`을 쥔
        웹 쓰기 스레드와 같은 파일을 동시에 load/save 했고 나중에 저장한 쪽이
        먼저 저장한 쪽의 등록 건을 통째로 덮어썼다. (큐에서는 이미 지워진 뒤였다.)
        """
        if self._pipeline_block():
            return 0

        with self._pending_lock:
            if not self._pending_queue:
                return 0
            queue_snapshot = list(self._pending_queue)

        master_excel = self.find_master_excel()
        if not master_excel or self.is_file_locked(master_excel):
            return 0

        applied = 0
        applied_ids = set()
        conflicts = []
        deferred = []
        with self.lock:
            # 여러 작업을 한꺼번에 재생하므로 60초 간격 제한과 무관하게 직전 본을 남긴다.
            try:
                from extractors.ledger_parser import backup_master_excel
                backup_master_excel(master_excel, self.base_dir, force=True)
            except ProductionWriteBlocked:
                return 0
            except Exception as e:
                print(f"[보류 큐 백업 경고] {e}")
            self._ensure_ledger_ids(master_excel)
            before_mtime = master_excel.stat().st_mtime
            for idx, op in enumerate(queue_snapshot):
                item = op.get("item") or {}
                try:
                    ok = self._apply_to_excel(master_excel, item, op.get("action"))
                except ExcelApplyConflict as conflict:
                    # 다른 항목의 행을 가리키는 작업. 재시도해도 결과가 같고, 적용하면 실제
                    # 대장 행이 파괴된다. 바로 격리하고 다음 작업으로 넘어간다. (감사 R3-02)
                    op["quarantine_detail"] = str(conflict)
                    conflicts.append(op)
                    print(f"[보류 큐 충돌] {op.get('action')} {item.get('ledger_id')}: {conflict}")
                    continue
                except ProductionWriteBlocked:
                    break
                except Exception as e:
                    ok = False
                    print(f"[보류 큐 반영 오류] {op.get('action')} {item.get('ledger_id')}: {e}")
                if ok:
                    applied += 1
                    applied_ids.add(op.get("operation_id"))
                    self._record_history(str(item.get("ledger_id") or ""), "EXCEL_APPLIED", item)
                    continue
                if op.get("action") == "delete":
                    # 지울 행이 이미 없다. 재시도해도 할 일이 없으므로 완료로 처리한다.
                    applied_ids.add(op.get("operation_id"))
                    if len(self._missing_delete_ids) >= 256:
                        self._missing_delete_ids.pop()
                    self._missing_delete_ids.add(op.get("operation_id"))
                    continue
                op["attempts"] = int(op.get("attempts", 0)) + 1
                # 실패 이후의 작업은 손대지 않고 순서를 보존한다.
                deferred = queue_snapshot[idx:]
                break

            if applied > 0:
                self._mark_own_write(before_mtime, master_excel.stat().st_mtime)

        # 반영에 성공한 작업만 큐에서 뺀다. flush 도중 새로 들어온 작업은 남긴다.
        # 큐 제거는 `wb.save()`가 끝난 뒤에만 일어난다.
        self._retire_pending_operations(applied_ids, deferred, conflicts)

        if applied > 0:
            self.bump_version()
            print(f"✓ 대기 중이던 엑셀 동기화 {applied}건 일괄 반영 완료!")
        return applied

    def _retire_pending_operations(self, applied_ids: set, deferred: List[Dict[str, Any]],
                                   conflicts: Optional[List[Dict[str, Any]]] = None):
        """반영에 성공한 작업을 큐에서 제거하고, 충돌·재시도 한도 초과 작업은 격리한다."""
        conflicts = conflicts or []
        if not applied_ids and not deferred and not conflicts:
            return
        with self._pending_lock:
            expired = [op for op in self._pending_queue
                       if op.get("operation_id") not in applied_ids
                       and int(op.get("attempts", 0)) >= MAX_PENDING_ATTEMPTS
                       and op not in conflicts]
        if conflicts and not self._quarantine_pending_operations(conflicts, QUARANTINE_CONFLICT):
            return
        if expired and not self._quarantine_pending_operations(expired, QUARANTINE_RETRY_LIMIT):
            return
        retired = applied_ids | {op.get("operation_id") for op in conflicts + expired}
        with self._pending_lock:
            previous_queue = self._pending_queue
            self._pending_queue = [op for op in previous_queue if op.get("operation_id") not in retired]
            if not self._save_pending_queue():
                self._pending_queue = previous_queue

    def _failed_path(self) -> Path:
        return self.base_dir / ".excel_pending_failed.json"

    def _quarantine_pending_operations(self, operations: List[Dict[str, Any]],
                                       reason: str = QUARANTINE_RETRY_LIMIT, detail: str = ""):
        """반영할 수 없는 작업을 별도 파일로 옮긴다. 큐 선두가 영원히 막히는 것을 막는다."""
        path = self._failed_path()
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        records = []
        for op in operations:
            rec = dict(op)
            rec["quarantine_reason"] = reason
            rec["quarantined_at"] = stamp
            if detail and not rec.get("quarantine_detail"):
                rec["quarantine_detail"] = detail
            records.append(rec)
        try:
            ensure_writable(path, "엑셀 격리 작업 파일")
            # 읽고-추가하고-쓰는 사이에 화면의 재시도·삭제가 끼어들면 한쪽 변경이 사라진다.
            with self._failed_lock:
                existing = self._quarantined_operations()
                known = {op.get("operation_id") for op in existing}
                existing.extend(op for op in records if op.get("operation_id") not in known)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, path)
        except Exception as e:
            print(f"[보류 큐 격리 경고] 실패 작업 기록 실패 ({path}): {e}")
            return False
        ids = ", ".join(str((op.get("item") or {}).get("ledger_id") or "?") for op in operations)
        if reason == QUARANTINE_CONFLICT:
            print(
                f"⚠️ [보류 큐] 엑셀의 같은 대장ID 행이 다른 요구자료라서 반영하지 않은 작업 "
                f"{len(operations)}건을 격리했습니다: {ids}\n"
                f"   엑셀 행은 그대로 두었습니다. {path.name}의 quarantine_detail을 확인하세요."
            )
        else:
            print(
                f"⚠️ [보류 큐] {MAX_PENDING_ATTEMPTS}회 재시도에도 엑셀에 반영하지 못한 작업 "
                f"{len(operations)}건을 격리했습니다: {ids}\n"
                f"   DB 값은 엑셀 동기화에서 보호됩니다. 엑셀을 수기로 맞춘 뒤 {path.name}에서 해당 항목을 지우세요."
            )

        return True

    def failed_count(self) -> int:
        """격리된 엑셀 반영 작업 수(충돌 + 재시도 한도 초과). 상태 API 노출용."""
        return len(self._quarantined_operations())

    # -------------------------------------------------------------------------
    # 격리 작업 관리 (화면에서 확인·재시도·삭제). 예전에는 파일을 직접 편집해야 했다.
    # -------------------------------------------------------------------------
    @staticmethod
    def _quarantine_summary(op: Dict[str, Any]) -> Dict[str, Any]:
        item = op.get("item") or {}
        return {
            "operation_id": op.get("operation_id"),
            "action": op.get("action"),
            "ledger_id": item.get("ledger_id"),
            "title": item.get("title"),
            "requester": item.get("requester"),
            "changed_fields": item.get("_changed_fields"),
            "attempts": op.get("attempts", 0),
            "quarantine_reason": op.get("quarantine_reason"),
            "quarantine_detail": op.get("quarantine_detail", ""),
            "quarantined_at": op.get("quarantined_at"),
        }

    def list_quarantined(self) -> List[Dict[str, Any]]:
        return [self._quarantine_summary(op) for op in self._quarantined_operations()]

    def _write_quarantine_file(self, operations: List[Dict[str, Any]]) -> bool:
        path = self._failed_path()
        tmp = path.with_suffix(".json.tmp")
        try:
            ensure_writable(path, "엑셀 격리 작업 파일")
            if operations:
                tmp.write_text(json.dumps(operations, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, path)
            elif path.exists():
                path.unlink()
            return True
        except Exception as e:
            print(f"[보류 큐 격리 경고] 격리 파일 갱신 실패 ({path}): {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _take_quarantined(self, operation_id: str):
        operations = self._quarantined_operations()
        for idx, op in enumerate(operations):
            if op.get("operation_id") == operation_id:
                return op, operations[:idx] + operations[idx + 1:]
        return None, operations

    def retry_quarantined(self, operation_id: str) -> Dict[str, Any]:
        """격리 작업을 보류 큐로 되돌린다(시도 횟수 초기화). 반영은 다음 flush에서 한다.

        큐에 먼저 넣고 나서 격리 파일에서 뺀다. 중간에 실패해도 작업이 사라지지 않고,
        최악의 경우 양쪽에 남아 한 번 더 보호될 뿐이다.
        """
        with self._failed_lock:
            op, rest = self._take_quarantined(str(operation_id or ""))
            if op is None:
                return {"success": False, "error": "해당 격리 작업을 찾을 수 없습니다."}
            revived = {k: v for k, v in op.items()
                       if k not in ("quarantine_reason", "quarantine_detail", "quarantined_at")}
            revived["attempts"] = 0
            with self._pending_lock:
                self._pending_queue.append(revived)
                if not self._save_pending_queue():
                    self._pending_queue.remove(revived)
                    return {"success": False, "error": "보류 큐를 저장하지 못했습니다."}
            if not self._write_quarantine_file(rest):
                return {"success": False, "error": "보류 큐에는 넣었지만 격리 파일을 갱신하지 못했습니다."}
        self.bump_version()
        return {"success": True, "message": "보류 큐로 되돌렸습니다. 마스터 엑셀이 닫혀 있으면 곧 반영됩니다."}

    def dismiss_quarantined(self, operation_id: str) -> Dict[str, Any]:
        """격리 작업을 버린다. 재시도 한도 초과 작업이면 그 필드의 DB 보호도 함께 풀린다."""
        with self._failed_lock:
            op, rest = self._take_quarantined(str(operation_id or ""))
            if op is None:
                return {"success": False, "error": "해당 격리 작업을 찾을 수 없습니다."}
            if not self._write_quarantine_file(rest):
                return {"success": False, "error": "격리 파일을 갱신하지 못했습니다."}
        item = op.get("item") or {}
        self._record_history(str(item.get("ledger_id") or ""), "EXCEL_QUARANTINE_DISMISSED", item)
        self.bump_version()
        return {"success": True, "message": "격리 작업을 삭제했습니다."}
