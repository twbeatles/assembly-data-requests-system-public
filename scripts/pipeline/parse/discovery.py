# -*- coding: utf-8 -*-
"""파싱 대상 파일 탐색. 경로는 parse_all 모듈 전역을 호출 시점에 읽는다."""
from typing import Optional
import os
from pathlib import Path


def get_exclude_dirs():
    import parse_all as pa
    extra = pa.cfg.get("exclude_dirs") or pa.system_config.get_config(pa.SYSTEM_DIR).get("exclude_dirs") or []
    return pa.GENERIC_EXCLUDE_DIRS | set(extra)


def is_excluded_dir(dname: str, full_path: Optional[Path] = None) -> bool:
    import parse_all as pa
    if dname in get_exclude_dirs() or dname.startswith('.'):
        return True
    if dname.startswith('★'):
        return True
    if pa.ROOT_DIR != pa.SYSTEM_DIR and dname == pa.SYSTEM_DIR.name:
        return True
    return False


def find_target_files(root: Path):
    import parse_all as pa
    target_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_excluded_dir(d)]
        rel_dir = os.path.relpath(dirpath, root)
        parts = Path(rel_dir).parts
        if any(is_excluded_dir(p) for p in parts):
            continue

        for f in filenames:
            if any(f.startswith(prefix) for prefix in pa.EXCLUDE_FILE_PREFIXES):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in pa.TARGET_EXTENSIONS:
                target_files.append(Path(dirpath) / f)
    return target_files
