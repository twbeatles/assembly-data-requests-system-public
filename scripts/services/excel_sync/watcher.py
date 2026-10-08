# -*- coding: utf-8 -*-
import sys
import time
import threading
from pathlib import Path
from typing import List, Optional, Tuple

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

    _HostBase_ExcelWatcherMixin = ExcelSyncService
else:
    _HostBase_ExcelWatcherMixin = object


class ExcelWatcherMixin(_HostBase_ExcelWatcherMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    def check_and_sync_startup(self):
        """Called when web server or pipeline starts: ensures DB has the latest Excel changes.

        보류 큐를 먼저 반영한다. 큐를 남겨 둔 채 Excel→DB 동기화를 먼저 하면,
        아직 Excel에 없는 웹 등록이 '삭제된 행'으로 판정되고 웹 수정이 Excel
        구값으로 되돌아간다.
        """
        master_excel = self.find_master_excel()
        if not master_excel or not master_excel.exists():
            return

        excel_mtime_before = master_excel.stat().st_mtime
        flushed = 0
        try:
            flushed = self.flush_pending_queue()
            if flushed:
                print(f"✓ 시작 시 보류 중이던 엑셀 반영 {flushed}건을 먼저 처리했습니다.")
        except Exception as e:
            self._startup_error = f"보류 큐 선반영 실패: {e}"
            print(f"🚨 [시작 동기화] {self._startup_error}")

        master_excel = self.find_master_excel()
        if not master_excel or not master_excel.exists():
            return
        excel_mtime = master_excel.stat().st_mtime

        # If DB exists, check if Excel was modified more recently than DB
        if self.db_path.exists():
            db_mtime = self.db_path.stat().st_mtime
            # 큐 반영이 엑셀(과 DB 이력)을 방금 다시 썼다면 mtime 비교로는 서버가 꺼진 동안의
            # 수기 수정을 가려낼 수 없다. 이 경우와 flush 전 엑셀이 DB보다 새로웠던 경우 모두
            # 동기화한다.
            if flushed or self._external_change_pending or excel_mtime_before > db_mtime or excel_mtime > db_mtime:
                print(f"ℹ️ 엑셀 파일 수기 수정 감지 ({master_excel.name}). 시작 전 DB 동기화 진행...")
                res = self.sync_excel_to_db()
                if not res.get("success"):
                    # 동기화를 건너뛴 경우 mtime을 최신으로 기록하면 watcher가
                    # 이 변경을 영구히 놓친다.
                    return
            self._last_known_excel_mtime = excel_mtime
        else:
            self.sync_excel_to_db()

    def start_file_watcher(self, poll_interval: float = 3.0):
        """Starts a background daemon thread that monitors Excel saves in real-time."""
        if self._watcher_running:
            return

        def _watcher_loop():
            self._watcher_running = True
            last_error = None
            while self._watcher_running:
                try:
                    time.sleep(poll_interval)

                    # 파이프라인이 DB를 재구축하는 중에는 아무것도 하지 않는다.
                    if self._pipeline_block():
                        continue

                    master_excel = self.find_master_excel()
                    if not master_excel or not master_excel.exists():
                        continue

                    # 외부(사용자) 저장을 보류 큐 반영보다 먼저 읽는다. 순서를
                    # 반대로 하면 flush가 저장한 mtime에 가려 사용자의 수기 수정이
                    # DB에 영구히 반영되지 않는다.
                    #
                    # 엑셀이 파일을 열고 있어도 읽기는 된다. 예전에는 잠겨 있으면 건너뛰어,
                    # Ctrl+S로 저장만 하고 엑셀을 닫지 않으면 동기화가 전혀 일어나지 않았다.
                    # (감사 R3-09) ID 스탬핑(쓰기)만 잠금이 풀린 뒤로 미뤄진다.
                    current_mtime = master_excel.stat().st_mtime
                    external_save = (
                        current_mtime > self._last_known_excel_mtime
                        and current_mtime != self._last_web_write_time
                    )
                    if external_save or self._external_change_pending:
                        print(f"\n[실시간 감지] 마스터 엑셀 저장 감지 ({master_excel.name}) ➔ DB 동기화 실행 중...")
                        self.sync_excel_to_db()

                    # 그다음에 잠금 때문에 미뤘던 웹 변경을 엑셀에 기록한다.
                    if not self.is_file_locked(master_excel):
                        self.flush_pending_queue()
                    last_error = None
                except Exception as e:
                    # 저장 도중의 일시 오류는 다음 주기에 다시 시도한다. 다만 같은 오류가 반복돼도
                    # 아무 흔적이 없으면 감시가 멈춘 줄 모른다. 오류가 바뀔 때만 한 번 출력한다.
                    message = f"{type(e).__name__}: {e}"
                    if message != last_error:
                        print(f"[엑셀 감시 알림] {message} (다음 주기에 다시 시도합니다)")
                        last_error = message

        t = threading.Thread(target=_watcher_loop, daemon=True, name="ExcelWatcherThread")
        self._watcher_thread = t
        t.start()
        print("✓ 마스터 엑셀 실시간 감시 스레드 가동 완료")

    def stop_file_watcher(self):
        self._watcher_running = False

    def startup_error(self) -> Optional[str]:
        """서버 시작 동기화에서 난 오류. 없으면 None. `/api/sync`로 노출한다. (감사 R4-15e)"""
        return getattr(self, "_startup_error", None)

    def record_startup_error(self, message: str):
        self._startup_error = message
