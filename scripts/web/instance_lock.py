# -*- coding: utf-8 -*-
"""02번 웹 관리 서버의 단일 인스턴스 락.

같은 시스템 폴더에서 서버가 두 개 뜨면 각자 엑셀 watcher를 돌리며 같은 마스터 엑셀을
openpyxl로 통째로 다시 쓰고, 같은 보류 큐 파일을 마지막 쓴 쪽 기준으로 덮어쓴다.
`ExcelSyncService.lock`은 프로세스 안에서만 유효하므로 프로세스 간 쓰기는 직렬화되지 않는다.
예전에는 포트가 이미 쓰이면 조용히 다음 포트로 떠서 이 상황이 쉽게 생겼다. (감사 R4-05)

검사·회수·생성·port 갱신·release는 고정 OS 잠금 아래 직렬화한다. 고정 OS 잠금 파일은
삭제하지 않는다. 예전에는 stale read와 unlink 사이에 경합이 있어, 비정상 종료 후
겹친 두 실행이 살아 있는 새 소유자의 락을 지우고 둘 다 취득 성공했다. (감사 ISSUE-001)
"""

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

from pipeline_guard import pid_is_alive
from write_guard import ensure_writable

LOCK_NAME = ".web_server.lock"
GUARD_SUFFIX = ".guard"
_LOCAL_LOCK = threading.RLock()


class ServerInstanceLock:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / LOCK_NAME
        self.guard_path = Path(str(self.path) + GUARD_SUFFIX)
        self._owned = False

    def read(self) -> Optional[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _holder_alive(self, info: Optional[dict]) -> bool:
        if not info:
            return False
        try:
            pid = int(info.get("pid") or 0)
        except (TypeError, ValueError):
            return False
        return pid > 0 and pid != os.getpid() and pid_is_alive(pid)

    @contextlib.contextmanager
    def _metadata_lock(self) -> Iterator[None]:
        """락 파일 검사·회수·생성을 프로세스 간에 직렬화한다.

        고정 OS 잠금 파일을 삭제하지 않는다. 잠금 파일 자체를 unlink하면
        잠금을 잡기 전에 두 소유자가 생긴다. (`PipelineGuard`와 같은 원칙)
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        with _LOCAL_LOCK, open(str(self.guard_path), "a+b") as stream:
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
                raise RuntimeError("웹 서버 락 확인 중입니다. 잠시 후 다시 시도하세요.") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _write(self, info: dict, exclusive: bool):
        flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
        fd = os.open(str(self.path), flags)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(info, fp, ensure_ascii=False)

    def _is_mine(self, info: Optional[dict]) -> bool:
        return bool(info) and str(info.get("pid")) == str(os.getpid())

    def acquire(self) -> Tuple[bool, Optional[dict]]:
        """(획득 여부, 이미 실행 중인 서버 정보). 죽은 프로세스가 남긴 락은 회수한다.

        같은 파일·소유자 확인 없이 경로를 unlink하지 않는다. 읽기·생존 확인·삭제·생성을
        고정 OS 잠금 안에서 한 번에 수행하므로, 겹친 두 실행 중 정확히 하나만 성공한다.
        """
        ensure_writable(self.path, "웹 서버 실행 락")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # OS 잠금은 마이크로초 단위로 끝나지만, 동시에 뜬 실행들은 여기서 겹친다.
        # 경합은 잠깐씩 쉬며 다시 본다. 곧바로 실패하면 상대가 회수 중인데도
        # 이 실행이 종료 코드 3으로 끝나고, 오래된 파일 내용을 들고 올 수 있다.
        for attempt in range(10):
            try:
                with self._metadata_lock():
                    try:
                        self._write({"pid": os.getpid(), "port": None}, exclusive=True)
                    except FileExistsError:
                        info = self.read()
                        if self._holder_alive(info):
                            return False, info
                        if self._is_mine(info):
                            self._owned = True
                            return True, None
                        # 비정상 종료로 남은 락. 같은 잠금 안에서 지우고 곧바로 다시 만든다.
                        # 바깥에서 먼저 읽은 stale 정보로는 절대 unlink하지 않는다.
                        try:
                            self.path.unlink()
                        except FileNotFoundError:
                            pass
                        except OSError:
                            return False, info
                        try:
                            self._write({"pid": os.getpid(), "port": None}, exclusive=True)
                        except FileExistsError:
                            continue
                    self._owned = True
                    return True, None
            except RuntimeError:
                time.sleep(0.005 * (attempt + 1))
                continue
        return False, self.read()

    def set_port(self, port: int):
        if not self._owned:
            return
        try:
            with self._metadata_lock():
                info = self.read()
                if not self._is_mine(info):
                    self._owned = False
                    return
                self._write({"pid": os.getpid(), "port": int(port)}, exclusive=False)
        except (OSError, RuntimeError):
            return

    def release(self):
        if not self._owned:
            return
        try:
            with self._metadata_lock():
                info = self.read()
                if info is None or self._is_mine(info):
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
        except RuntimeError:
            pass
        finally:
            self._owned = False
