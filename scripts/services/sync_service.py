# -*- coding: utf-8 -*-
"""
Pipeline Synchronization Service (SOLID: SRP).
Handles concurrency locks and asynchronous execution of the incremental data pipeline.
"""

import sys
import time
import datetime
import subprocess
import threading
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent.parent

class SyncService:
    """Manages background pipeline synchronization runs with concurrency prevention and status tracking."""

    def __init__(self, scripts_dir: Path = SCRIPTS_DIR, timeout_seconds: int = 1800):
        self.scripts_dir = Path(scripts_dir)
        self.timeout_seconds = timeout_seconds
        self.sync_lock = threading.Lock()
        self.is_syncing = False
        self.last_start_time: Optional[str] = None
        self.last_finish_time: Optional[str] = None
        self.last_success: Optional[bool] = None
        self.last_error: Optional[str] = None
        self.last_elapsed: float = 0.0

    def trigger_async_sync(self) -> Tuple[bool, str]:
        """Triggers pipeline in background if not already running. Returns (started, message)."""
        with self.sync_lock:
            if self.is_syncing:
                return False, "동기화 파이프라인이 이미 실행 중입니다. 잠시 후 다시 시도해주세요."
            self.is_syncing = True
            self.last_start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_finish_time = None
            self.last_success = None
            self.last_error = None
            self.last_elapsed = 0.0

        def _worker():
            t0 = time.time()
            success = False
            err = None
            try:
                pipeline_script = self.scripts_dir / "add_documents_smart.py"
                if not pipeline_script.exists():
                    pipeline_script = self.scripts_dir / "run_pipeline.py"
                if pipeline_script.exists():
                    res = subprocess.run(
                        [sys.executable, "-u", str(pipeline_script)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.timeout_seconds,
                        cwd=str(self.scripts_dir.parent)
                    )
                    if res.returncode == 0:
                        success = True
                    else:
                        err = f"파이프라인 오류(코드 {res.returncode}): {res.stderr[:200] if res.stderr else '알 수 없는 오류'}"
                else:
                    err = "파이프라인 실행 스크립트를 찾을 수 없습니다."
            except subprocess.TimeoutExpired:
                err = f"파이프라인이 {self.timeout_seconds}초를 초과하여 중단되었습니다."
            except Exception as e:
                err = str(e)
            finally:
                elapsed = time.time() - t0
                with self.sync_lock:
                    self.is_syncing = False
                    self.last_finish_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.last_success = success
                    self.last_error = err
                    self.last_elapsed = round(elapsed, 2)

        threading.Thread(target=_worker, daemon=True).start()
        return True, "신규 문서 파싱 및 전체 DB 동기화 작업이 백그라운드에서 시작되었습니다."

    def get_status(self) -> Dict[str, Any]:
        """Returns current synchronization status and last run metrics."""
        with self.sync_lock:
            return {
                "success": True,
                "is_syncing": self.is_syncing,
                "last_start_time": self.last_start_time,
                "last_finish_time": self.last_finish_time,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "last_elapsed": self.last_elapsed
            }
