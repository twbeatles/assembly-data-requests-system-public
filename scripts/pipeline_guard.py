# -*- coding: utf-8 -*-
"""파이프라인 실행 중 웹 CRUD와의 경합을 막기 위한 파일 락."""

import os
import time
import threading
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".pipeline.lock"
STALE_SECONDS = 4 * 3600
_OWNERS = {}
_LOCAL_LOCK = threading.RLock()


def pid_is_alive(pid: int) -> bool:
    """락 파일에 적힌 PID가 실제로 살아 있는지 본다."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    """OpenProcess만으로는 종료 직후 핸들이 남아 살아 있는 것처럼 보일 수 있다."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # 5: 프로세스는 있으나 권한이 없음. 그 외(87 등)는 PID가 없다.
        return kernel32.GetLastError() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class PipelineGuard:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / LOCK_NAME
        self._key = str(self.path.resolve())
        self._identity = None

    def _lock_pid(self):
        """락 파일의 PID. 없거나 깨졌으면 None."""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        if not text.isdigit():
            return None
        return int(text)

    @contextmanager
    def _metadata_lock(self):
        # Keep this inode: unlinking an OS lock would allow two owners.
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with _LOCAL_LOCK, open(str(self.path) + ".guard", "a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("파이프라인 락 확인 중입니다. 잠시 후 다시 시도하세요.") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _signature(self):
        stat = self.path.stat()
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def is_stale(self) -> bool:
        if not self.path.exists():
            return False
        pid = self._lock_pid()
        if pid is not None:
            return not pid_is_alive(pid)
        try:
            # A just-created file may not have its PID yet. Fail closed.
            return time.time() - self.path.stat().st_mtime > STALE_SECONDS
        except OSError:
            return False

    def is_mine(self) -> bool:
        owner = _OWNERS.get(self._key)
        return bool(owner and owner[0] == threading.get_ident()
                    and self._lock_pid() == os.getpid())

    def is_locked(self) -> bool:
        if not self.path.exists():
            return False
        try:
            with self._metadata_lock():
                return self.path.exists() and not self.is_stale()
        except (RuntimeError, OSError):
            return True

    def acquire(self, allow_reentrant: bool = True) -> bool:
        with self._metadata_lock():
            if self.path.exists():
                if self.is_stale():
                    self.path.unlink()
                elif allow_reentrant and self.is_mine():
                    return False
                else:
                    raise RuntimeError("문서 동기화 파이프라인이 실행 중입니다. 완료 후 다시 시도해주세요.")
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
            self._identity = self._signature()
            _OWNERS[self._key] = (threading.get_ident(), self._identity)
            return True

    def release(self):
        if self._identity is None:
            return
        with self._metadata_lock():
            if (self.path.exists() and self._signature() == self._identity
                    and self._lock_pid() == os.getpid()):
                self.path.unlink()
            if _OWNERS.get(self._key) == (threading.get_ident(), self._identity):
                _OWNERS.pop(self._key, None)
            self._identity = None
