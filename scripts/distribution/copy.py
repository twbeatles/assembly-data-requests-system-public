# -*- coding: utf-8 -*-
"""배포 파일 복사 헬퍼(SRP: 재시도 복사)."""

import shutil
import time
from pathlib import Path


def safe_copy_file(src: Path, dst: Path, max_retries=5, delay=0.3):
    for _i in range(max_retries):
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


# 변환 중단 복구용 전이 파일. 배포본에 옮기면 다음 파싱이 옛 본문으로 "복구"한다.
TRANSIENT_SUFFIXES = (".previous", ".tmp")


def mirror_markdown_tree(src: Path, dst: Path) -> int:
    """`src`를 `dst`에 미러링한다. 원본에 없는 파일은 지운다. 지운 파일 수를 돌려준다.

    데이터 배포본은 원문이 없어 파서가 원문 없는 마크다운을 보존한다. 덮어쓰기 복사만 하면
    원본에서 지우거나 이름을 바꾼 문서의 마크다운이 배포본에 영구히 남아, 배포본에서 00번을
    실행할 때 문서가 되살아나거나 두 건이 된다. (2026-10-21 감사 ISSUE-004)
    """
    src, dst = Path(src), Path(dst)
    wanted = set()
    for path in src.rglob("*"):
        if not path.is_file() or path.name.endswith(TRANSIENT_SUFFIXES):
            continue
        rel = path.relative_to(src)
        wanted.add(rel)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_copy_file(path, target)
    removed = 0
    if dst.exists():
        for path in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() and path.relative_to(dst) not in wanted:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return removed
