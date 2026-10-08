# -*- coding: utf-8 -*-
"""배포 파일 복사 헬퍼(SRP: 재시도 복사)."""
import shutil
import time
from pathlib import Path


def safe_copy_file(src: Path, dst: Path, max_retries=5, delay=0.3):
    for i in range(max_retries):
        try:
            shutil.copy2(src, dst)
            return True
        except (PermissionError, OSError):
            time.sleep(delay)
    try:
        shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"경고: 파일 복사 실패 ({src.name} -> {dst.name}): {e}")
        return False
