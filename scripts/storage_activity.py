"""Serialize storage mutations with rebuilds, across threads and processes.

Order: activity -> pipeline metadata -> workbook -> failed -> pending -> SQLite.
The fixed guard file is never removed. Reentry is allowed only on the same thread.
"""

import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from write_guard import ensure_writable

_REGISTRY_LOCK = threading.Lock()
_LOCKS = {}
_HELD = threading.local()


class StorageBusyError(RuntimeError):
    pass


@contextmanager
def storage_activity(base_dir, timeout=15.0, create=True):
    path = Path(base_dir).resolve() / ".storage_activity.guard"
    key = os.path.normcase(str(path))
    held = getattr(_HELD, "paths", None)
    if held is None:
        held = _HELD.paths = set()
    if key in held:
        yield
        return
    if not create and not path.exists():
        # Legacy read-only images may not have a gate. They keep the explicit
        # immutable contract; every managed writable schema creates the gate.
        yield
        return
    if create:
        ensure_writable(path, "저장 작업 실행 락")
    with _REGISTRY_LOCK:
        local_lock = _LOCKS.setdefault(key, threading.Lock())
    deadline = time.monotonic() + max(0.0, timeout)
    if not local_lock.acquire(timeout=max(0.0, timeout)):
        raise StorageBusyError(
            "다른 저장 또는 문서 갱신이 진행 중입니다. 잠시 후 다시 시도하세요."
        )
    stream = None
    locked = False
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b" if create else "rb")
        stream.seek(0, os.SEEK_END)
        if not stream.tell() and create:
            stream.write(b"0")
            stream.flush()
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StorageBusyError(
                        "다른 저장 또는 문서 갱신이 진행 중입니다. 잠시 후 다시 시도하세요."
                    ) from exc
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        held.add(key)
        yield
    finally:
        held.discard(key)
        try:
            if locked and stream is not None:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            if stream is not None:
                stream.close()
            local_lock.release()


def guarded_write(kind="ledger"):
    """Hold the activity gate through commit, delivery and derived JSON writes."""

    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            # 파이프라인 실행과 다른 저장 작업(큰 엑셀 저장 등)의 대기 초과를 구분해 알린다.
            code = "storage_busy"
            try:
                from pipeline_guard import PipelineGuard

                pipeline = PipelineGuard(self.base_dir)
                if pipeline.is_locked() and not pipeline.is_mine():
                    code = "pipeline_busy"
                    raise StorageBusyError(
                        "문서 동기화 파이프라인이 실행 중입니다. 완료 후 다시 시도해주세요."
                    )
                with storage_activity(self.base_dir):
                    return method(self, *args, **kwargs)
            except StorageBusyError as exc:
                if kind == "count":
                    return 0
                if kind == "raise":
                    raise
                result = {"success": False, "code": code}
                if kind == "sync":
                    result.update(skipped=True, message=str(exc))
                else:
                    result["error"] = str(exc)
                return result

        return guarded

    return decorate
