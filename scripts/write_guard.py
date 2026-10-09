# -*- coding: utf-8 -*-
"""테스트 실행 중 운영 데이터(마스터 엑셀·DB·JSON·보류 큐·설정) 쓰기를 막는다.

감사 R3-01: 테스트가 DB 경로만 임시 파일로 바꾸고 `base_dir`는 기본값(운영 폴더)을
그대로 써서, 테스트 페이로드가 실제 국회 요구자료 대장과 보류 큐에 쌓였다. 경로 기본값을
고치는 것과 별개로, 같은 사고가 다시 나지 않도록 쓰기 지점에서 한 번 더 막는다.

보호 대상은 `tests/` 폴더를 가진 시스템 폴더(= 이 소스가 실제로 설치된 곳)다. 테스트가
임시 디렉터리에 `scripts`만 복사해 만든 가짜 시스템 폴더는 보호 대상이 아니다.
"""

import os
import re
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent


class ProductionWriteBlocked(RuntimeError):
    """테스트 실행 중 운영 폴더에 쓰려고 할 때 발생한다.

    PermissionError를 상속하지 않는다. 호출부의 PermissionError 처리(보류 큐 적재)가
    이 예외를 "엑셀 잠김"으로 오인해 운영 큐에 다시 쓰는 일을 막기 위해서다.
    """


def is_test_run() -> bool:
    flag = os.environ.get("DATAREQ_TEST_MODE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    joined = " ".join(sys.argv).lower()
    return "unittest" in joined or "pytest" in joined


def protected_root():
    """보호할 운영 시스템 폴더. `tests/`가 없는 복사본이면 None."""
    if (SYSTEM_DIR / "tests").is_dir():
        return SYSTEM_DIR.resolve()
    return None


def protected_workspace(root=None):
    """연도 폴더(20xx)가 있는 상위 워크스페이스. 원문 HWP·PDF가 있는 곳이라 역시 보호한다.

    2026-09-15 운영 반영 중, 격리 장치 이전의 테스트가 `2026`·`미분류` 원문 폴더에 남긴
    가짜 HWP 2건이 파싱 실패로 드러났다.
    """
    root = root or protected_root()
    if root is None:
        return None
    parent = root.parent
    if parent == root:
        return None
    try:
        if any(child.is_dir() and re.fullmatch(r"20[1-3]\d", child.name) for child in parent.iterdir()):
            return parent
    except OSError:
        return None
    return None


def is_protected_path(path) -> bool:
    root = protected_root()
    if root is None or path is None:
        return False
    try:
        target = Path(path).resolve()
    except OSError:
        return False
    if target == root:
        return True
    try:
        rel = target.relative_to(root)
    except ValueError:
        workspace = protected_workspace(root)
        if workspace is None:
            return False
        try:
            target.relative_to(workspace)
        except ValueError:
            return False
        return True
    # 소스·테스트 폴더 자체는 운영 데이터가 아니다.
    return not rel.parts or rel.parts[0] not in ("scripts", "tests")


def ensure_writable(path, what: str = "운영 데이터") -> None:
    """테스트 실행 중이면 운영 폴더 경로에 대한 쓰기를 거부한다."""
    if is_test_run() and is_protected_path(path):
        raise ProductionWriteBlocked(
            f"[테스트 격리] 테스트 실행 중에는 운영 {what}에 쓸 수 없습니다: {path}\n"
            "테스트는 임시 디렉터리를 base_dir로 지정해야 합니다."
        )
