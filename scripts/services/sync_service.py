# -*- coding: utf-8 -*-
"""
Pipeline Synchronization Service (SOLID: SRP).
Handles concurrency locks and asynchronous execution of the incremental data pipeline.
"""

import datetime
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

SCRIPTS_DIR = Path(__file__).resolve().parent.parent


def _decode_preview(data) -> str:
    """자식 프로세스 출력 미리보기. bytes/str 어느 쪽으로 와도 다룬다."""
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).decode("utf-8", errors="replace")
    else:
        text = str(data or "")
    text = text.strip()
    return text[:200] if text else "알 수 없는 오류"


def _terminate_tree(proc: subprocess.Popen) -> None:
    """파이프라인 프로세스 트리째 끝낸다.

    `subprocess.run(timeout=...)`은 직접 자식만 죽인다. 파이프라인이
    `run_converter`로 띄운 KorDoc 변환기는 Windows에서 별도 프로세스 그룹이라
    살아남아 출력 파일을 계속 쥔다. 그래서 timeout·취소 때는 트리째 끝낸다.
    (`parse_all.run_converter`의 `_kill_process_tree`와 같은 규칙.
    PROJECT_AUDIT.md ISSUE-002)
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


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
        # 실행 중인 파이프라인 자식 핸들과 취소 요청. (ISSUE-002, GAP-001)
        self._proc_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_requested = False

    def trigger_async_sync(self) -> Tuple[bool, str]:
        """Triggers pipeline in background if not already running. Returns (started, message)."""
        with self.sync_lock:
            if self.is_syncing:
                return (
                    False,
                    "동기화 파이프라인이 이미 실행 중입니다. 잠시 후 다시 시도해주세요.",
                )
            self.is_syncing = True
            self.last_start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_finish_time = None
            self.last_success = None
            self.last_error = None
            self.last_elapsed = 0.0
        with self._proc_lock:
            self._cancel_requested = False
            self._proc = None

        def _worker():
            t0 = time.time()
            success = False
            err = None
            proc = None
            try:
                pipeline_script = self.scripts_dir / "add_documents_smart.py"
                if not pipeline_script.exists():
                    pipeline_script = self.scripts_dir / "run_pipeline.py"
                if pipeline_script.exists():
                    popen_kwargs: Dict[str, Any] = {}
                    if sys.platform != "win32":
                        # POSIX에서 트리째 끝내려면 자식을 별도 프로세스 그룹으로 둔다.
                        popen_kwargs["start_new_session"] = True
                    proc = subprocess.Popen(
                        [sys.executable, "-u", str(pipeline_script)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(self.scripts_dir.parent),
                        **popen_kwargs,
                    )
                    with self._proc_lock:
                        self._proc = proc
                    try:
                        _, stderr = proc.communicate(timeout=self.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        _terminate_tree(proc)
                        _, stderr = proc.communicate()
                        err = f"파이프라인이 {self.timeout_seconds}초를 초과하여 중단되었습니다."
                    else:
                        if proc.returncode == 0:
                            success = True
                        else:
                            err_text = _decode_preview(stderr)
                            err = f"파이프라인 오류(코드 {proc.returncode}): {err_text}"
                else:
                    err = "파이프라인 실행 스크립트를 찾을 수 없습니다."
            except Exception as e:
                err = str(e)
            finally:
                with self._proc_lock:
                    cancelled = self._cancel_requested
                    self._proc = None
                if cancelled:
                    success = False
                    err = "사용자가 동기화를 중단했습니다."
                elapsed = time.time() - t0
                with self.sync_lock:
                    self.is_syncing = False
                    self.last_finish_time = datetime.datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    self.last_success = success
                    self.last_error = err
                    self.last_elapsed = round(elapsed, 2)

        threading.Thread(target=_worker, daemon=True).start()
        return (
            True,
            "신규 문서 파싱 및 전체 DB 동기화 작업이 백그라운드에서 시작되었습니다.",
        )

    def cancel_sync(self) -> Tuple[bool, str]:
        """실행 중인 파이프라인 중단을 요청한다. 자식 트리째 끝낸다. (GAP-001)

        실행 중이 아니면 (False, 안내)를 돌려준다. 강제 종료가 아니라
        트리 종료 후 상태가 `is_syncing=False`·"사용자가 중단"으로 남는다.
        """
        with self.sync_lock:
            if not self.is_syncing:
                return False, "실행 중인 동기화 작업이 없습니다."
        with self._proc_lock:
            self._cancel_requested = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            _terminate_tree(proc)
        return True, "동기화 중단 요청을 전달했습니다. 잠시 후 상태를 확인하세요."

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
                "last_elapsed": self.last_elapsed,
            }
