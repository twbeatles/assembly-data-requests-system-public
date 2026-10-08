# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# 경로는 호출자가 준 root_dir로만 정한다. 예전에는 `extract_and_build_db` 모듈이 import돼
# 있기만 하면 그 모듈의 전역 경로(= 운영 폴더)를 돌려줘서, 임시 폴더만 넘긴 호출이 조용히
# 운영 DB·운영 설정·운영 대장 엑셀을 읽었다. (감사 R4-02) 모듈 전역은 root_dir이 없을 때만 쓴다.
def _get_active_system_dir(root_dir: Optional[Path] = None) -> Path:
    if root_dir:
        return Path(root_dir)
    mod = sys.modules.get("extract_and_build_db")
    if mod and hasattr(mod, "SYSTEM_DIR"):
        return Path(mod.SYSTEM_DIR)
    return DEFAULT_SYSTEM_DIR


def _get_active_db_path(root_dir: Optional[Path] = None) -> Path:
    if root_dir:
        return Path(root_dir) / "data_requests.db"
    mod = sys.modules.get("extract_and_build_db")
    if mod and hasattr(mod, "DB_PATH"):
        return Path(mod.DB_PATH)
    return DEFAULT_DB_PATH
