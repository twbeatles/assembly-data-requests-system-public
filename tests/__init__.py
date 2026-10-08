# 테스트 중 대시보드·브라우저 창이 열리지 않도록 한다.
import os
os.environ.setdefault("DATAREQ_NO_BROWSER", "1")

# ---------------------------------------------------------------------------
# 운영 SQLite 접근 기록 (감사 R4-01)
#
# 운영 DB를 읽기 전용(mode=ro)으로 열기만 해도 WAL 사이드카(-shm/-wal)가 바뀐다. 파일 지문
# 비교만으로는 이 접근이 실행 환경에 따라 잡히기도 하고 안 잡히기도 해서, 격리 테스트가
# 비결정적으로 실패했다. 연결 시점에 운영 DB 경로를 여는지 직접 기록하고,
# `test_zzz_production_isolation`이 목록이 비어 있는지 확인한다. 사이드카를 전혀 건드리지 않는
# `immutable=1` 연결만 허용한다.
#
# unittest discover와 pytest 모두 테스트 모듈보다 이 패키지를 먼저 import한다.
# ---------------------------------------------------------------------------
import sqlite3 as _sqlite3
import traceback as _traceback
from pathlib import Path as _Path
from urllib.parse import unquote as _unquote

_SYSTEM_DIR = _Path(__file__).resolve().parent.parent
PRODUCTION_DB_PATHS = tuple(
    str((_SYSTEM_DIR / name).resolve()).replace("/", "\\").lower()
    for name in ("data_requests.db",)
)
PRODUCTION_DB_OPENS = []


def _normalized_target(database) -> str:
    text = _unquote(os.fspath(database) if not isinstance(database, str) else database)
    if text.lower().startswith("file:"):
        text = text[5:]
    text = text.split("?", 1)[0]
    try:
        return str(_Path(text).resolve()).replace("/", "\\").lower()
    except (OSError, ValueError):
        return text.replace("/", "\\").lower()


def _install_guard():
    if getattr(_sqlite3.connect, "_datareq_production_guard", False):
        return
    original = _sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        try:
            raw = database if isinstance(database, str) else os.fspath(database)
            if _normalized_target(database) in PRODUCTION_DB_PATHS and "immutable=1" not in raw:
                frames = _traceback.extract_stack(limit=8)[:-1]
                where = " <- ".join(f"{_Path(f.filename).name}:{f.lineno}" for f in reversed(frames))
                PRODUCTION_DB_OPENS.append(where)
        except Exception:
            pass
        return original(database, *args, **kwargs)

    setattr(guarded_connect, "_datareq_production_guard", True)
    _sqlite3.connect = guarded_connect


_install_guard()
