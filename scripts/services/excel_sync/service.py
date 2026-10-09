# -*- coding: utf-8 -*-
import sys
import time
import threading
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

from services.excel_sync.identity import DEFAULT_SYSTEM_DIR
from services.excel_sync.pending import PendingQueueMixin
from services.excel_sync.apply import ExcelApplyMixin
from services.excel_sync.excel_to_db import ExcelToDbMixin
from services.excel_sync.watcher import ExcelWatcherMixin


class ExcelSyncService(PendingQueueMixin, ExcelApplyMixin, ExcelToDbMixin, ExcelWatcherMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    """Manages two-way sync between Excel ledgers and SQLite database."""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        json_path: Optional[Path] = None
    ):
        # base_dir를 생략하면 DB가 있는 폴더를 쓴다. 운영 폴더를 기본값으로 두면 테스트가
        # 운영 마스터 엑셀과 보류 큐에 쓴다. (감사 R3-01)
        if base_dir:
            self.base_dir = Path(base_dir)
        elif db_path:
            self.base_dir = Path(db_path).resolve().parent
        else:
            self.base_dir = DEFAULT_SYSTEM_DIR
        self.db_path = Path(db_path) if db_path else (self.base_dir / "data_requests.db")
        self.json_path = Path(json_path) if json_path else (self.base_dir / "data_requests.json")
        self.lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._missing_delete_ids = set()

        # Queue for writes delayed by Excel file locks
        self._pending_queue: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        # 격리 파일(.excel_pending_failed.json) 읽기-쓰기 직렬화.
        # 락 순서: self.lock → _failed_lock → _pending_lock (역순 금지, AGENTS.md 불변조건 8)
        self._failed_lock = threading.Lock()
        self._queue_corrupt: Optional[Dict[str, Any]] = None
        # 마지막 엑셀→DB 동기화 결과. /api/sync로 노출한다. (감사 R4-07)
        self._last_excel_sync: Optional[Dict[str, Any]] = None
        self._startup_error: Optional[str] = None

        # Timestamps to prevent sync echo loops
        self._last_web_write_time: float = 0.0
        self._last_known_excel_mtime: float = 0.0
        self._external_change_pending: bool = False
        self._watcher_thread: Optional[threading.Thread] = None
        self._watcher_running: bool = False
        self._version_counter: int = int(time.time())
        self._id_stamp_fingerprint = None
        self._master_warn_key = None
        self._load_pending_queue()

    @classmethod
    def get_instance(cls, base_dir: Optional[Path] = None):
        with cls._instance_lock:
            if cls._instance is None or (base_dir and cls._instance.base_dir.resolve() != Path(base_dir).resolve()):
                previous = cls._instance
                if previous is not None:
                    # 교체되는 인스턴스의 watcher가 고아 스레드로 남지 않게 한다.
                    previous.stop_file_watcher()
                cls._instance = cls(base_dir=base_dir)
            return cls._instance
