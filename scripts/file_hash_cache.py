# -*- coding: utf-8 -*-
"""원본 문서 SHA-256 캐시.

00번 한 번 실행에서 같은 원본(현재 754건, 약 424MB)을 다섯 번 해시하고 있었다.
parse_all이 캐시 확인과 파싱에서 두 번, extract_and_build_db가 JSON 매핑·DB 매핑·ID 배정에서
세 번이다. 파일 하나를 끝까지 읽는 비용이라, 변경이 없는 실행에서도 해시에만 30초 넘게 썼다.

- 프로세스 안: (경로, 크기, 수정시각 ns)가 같으면 한 번 계산한 값을 다시 쓴다.
- 실행 사이: 같은 키를 `.file_hash_cache.json`에 남겨, 바뀐 파일만 다시 읽는다.
- 크기나 수정시각이 달라지면 무조건 다시 계산한다. 수정된 지 2초가 안 된 파일은 캐시에
  남기지 않는다. 내용만 바뀌고 둘 다 그대로인 경우를 의심하면 `parse_all.py --force`가
  캐시를 믿지 않고 전부 다시 계산한다.

캐시 파일은 성능용 보조 자료다. 읽기·쓰기 실패는 조용히 무시하고 원래대로 해시한다.
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
CACHE_FILENAME = ".file_hash_cache.json"
CACHE_VERSION = 1
SETTLE_NS = 2_000_000_000


def compute_sha256(path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _cache_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


class FileHashCache:
    def __init__(self, cache_path=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self.trust_stat = True
        self._lock = threading.Lock()
        self._entries = {}
        self._dirty = False
        self._loaded = False
        self.hits = 0
        self.misses = 0

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return
        for key, entry in (data.get("files") or {}).items():
            if (isinstance(entry, list) and len(entry) == 3
                    and isinstance(entry[0], int) and isinstance(entry[1], int)
                    and isinstance(entry[2], str) and len(entry[2]) == 64):
                self._entries[key] = tuple(entry)

    def sha256(self, path) -> str:
        path = Path(path)
        st = path.stat()
        key = _cache_key(path)
        with self._lock:
            self._load()
            entry = self._entries.get(key)
            if self.trust_stat and entry and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                self.hits += 1
                return entry[2]
        digest = compute_sha256(path)
        # 읽는 동안 파일이 바뀌었으면 캐시에 남기지 않는다.
        try:
            after = path.stat()
        except OSError:
            return digest
        # 방금 수정된 파일은 같은 시각·같은 크기로 한 번 더 바뀔 수 있다(파일시스템 시각 해상도).
        # git의 racy 판정처럼, 수정 후 충분히 지난 파일만 캐시에 남긴다.
        settled = (time.time_ns() - st.st_mtime_ns) > SETTLE_NS
        with self._lock:
            self.misses += 1
            if settled and after.st_size == st.st_size and after.st_mtime_ns == st.st_mtime_ns:
                self._entries[key] = (st.st_size, st.st_mtime_ns, digest)
                self._dirty = True
        return digest

    def save(self) -> bool:
        """캐시를 원자적으로 기록한다. 없어진 파일 항목은 뺀다. 실패해도 예외를 내지 않는다."""
        if not self.cache_path:
            return False
        with self._lock:
            if not self._dirty:
                return False
            entries = {k: list(v) for k, v in self._entries.items() if os.path.exists(k)}
        try:
            from write_guard import ensure_writable, ProductionWriteBlocked
        except ImportError:  # pragma: no cover - scripts 경로 밖에서 쓰일 때
            ensure_writable, ProductionWriteBlocked = None, RuntimeError
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        try:
            if ensure_writable:
                ensure_writable(self.cache_path, "해시 캐시")
            tmp.write_text(json.dumps({"version": CACHE_VERSION, "files": entries},
                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except (OSError, ProductionWriteBlocked):
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
        with self._lock:
            self._dirty = False
        return True


_default = None
_default_lock = threading.Lock()


def default_cache() -> FileHashCache:
    global _default
    with _default_lock:
        if _default is None:
            _default = FileHashCache(SYSTEM_DIR / CACHE_FILENAME)
        return _default


def sha256_file(path) -> str:
    return default_cache().sha256(path)
