# -*- coding: utf-8 -*-
"""[T-ISO-1] 테스트 스위트가 운영 데이터 파일을 건드리지 않았는지 마지막에 확인한다.

감사 R3-01: 테스트가 운영 마스터 엑셀·DB·보류 큐에 테스트 페이로드를 써서 실제 국회
요구자료 대장이 오염됐다. unittest discover는 모든 테스트 모듈을 먼저 import한 뒤 파일명
순서로 실행하므로, 이 모듈의 import 시점 지문은 "어떤 테스트도 실행되기 전" 상태이고
이 모듈(`test_zzz_…`)의 테스트는 마지막에 실행된다.

감사 R4-01: SQLite WAL 사이드카(`-wal`/`-shm`)는 읽기 전용 연결로도 바뀐다. 크기·수정시각으로
비교하면 실행 환경에 따라 실패·통과가 갈렸다. 사이드카는 존재 여부만 비교하고, 운영 DB를
연 호출은 `tests/__init__.py`의 연결 기록으로 직접 확인한다.
"""

import sys
import unittest
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
if str(SYSTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEM_DIR))

# `unittest discover -s tests`는 테스트 파일을 최상위 모듈로 import해서 `tests/__init__.py`를
# 거치지 않는다. 두 러너 모두 모든 테스트 모듈을 import한 뒤에 실행하므로, 여기서 패키지를
# import해 운영 DB 연결 감시를 실행 전에 설치한다.
import tests as _tests_package  # noqa: E402,F401

WATCHED_PATTERNS = (
    "*.xlsm", "*.xlsx", "*.db", "*.json", "*.html",
    ".excel_pending_queue.json", ".excel_pending_failed.json", ".pipeline.lock", ".web_server.lock",
)
# 내용 대신 존재 여부만 본다. 테스트가 쓰기 연결을 열고 닫으면 사이드카가 생기거나 지워진다.
SIDECAR_PATTERNS = ("*.db-wal", "*.db-shm")
WATCHED_DIRS = (".ledger_backup",)


def fingerprint():
    snap = {}
    for pattern in WATCHED_PATTERNS:
        for path in SYSTEM_DIR.glob(pattern):
            if path.is_file():
                st = path.stat()
                snap[path.name] = (st.st_size, st.st_mtime_ns)
    for pattern in SIDECAR_PATTERNS:
        for path in SYSTEM_DIR.glob(pattern):
            if path.is_file():
                snap[path.name] = "exists"
    for name in WATCHED_DIRS:
        folder = SYSTEM_DIR / name
        if folder.is_dir():
            snap[name + "/"] = tuple(sorted(p.name for p in folder.iterdir()))
    return snap


BEFORE = fingerprint()


class TestProductionFilesUntouched(unittest.TestCase):
    def test_suite_did_not_modify_production_data(self):
        after = fingerprint()
        changed = sorted(
            name for name in set(BEFORE) | set(after)
            if BEFORE.get(name) != after.get(name)
        )
        self.assertEqual(
            changed, [],
            f"테스트 실행이 운영 폴더의 데이터 파일을 바꿨습니다: {changed}\n"
            "테스트는 임시 디렉터리를 base_dir/DB 경로로 지정해야 합니다.",
        )

    def test_suite_did_not_open_production_db(self):
        PRODUCTION_DB_OPENS = _tests_package.PRODUCTION_DB_OPENS
        self.assertEqual(
            PRODUCTION_DB_OPENS, [],
            "테스트가 운영 data_requests.db를 열었습니다(immutable 연결 제외). "
            "임시 DB 경로를 넘기거나 connect_readonly(..., immutable=True)를 쓰세요.\n"
            + "\n".join(PRODUCTION_DB_OPENS[:10]),
        )


if __name__ == "__main__":
    unittest.main()
