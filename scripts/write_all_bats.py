from typing import Any, cast
import os
import sys
import re
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config

WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
from bats.content import (
    MIN_PYTHON, PY_VERSION_CHECK, PY_DETECT, DEPS_CHECK,
    NO_PYTHON_REASON, NO_DEPS_BLOCK, _fill,
    SYSTEM_BAT_00, SYSTEM_BAT_01, SYSTEM_BAT_02, SYSTEM_BAT_03,
    SYSTEM_BAT_04, SYSTEM_BAT_05, SYSTEM_BAT_07, SYSTEM_BAT_06,
    DISTRIBUTED_BATS, SOURCE_ONLY_BATS,
)
from bats.validate import validate_batch_file_rules
DIST_DIR = SYSTEM_DIR / "배포용_자료요구_통합검색시스템"
UNIVERSAL_DIST_DIR = SYSTEM_DIR.parent / "국회자료요구_스마트시스템_범용배포용"





# ============================================================================
# Templates for SYSTEM_DIR & Distribution Packages (Relative to current dir)
# ============================================================================







# 배포본에도 포함한다. 더블클릭하면 폴더 선택 창이 열리고, 이전 폴더를 고르면
# 파싱 마크다운만 비파괴 병합하고 산출물을 재구축한다.

# 06은 원본 프로젝트 전용이다. 배포본에는 넣지 않는다. (배포본에서 돌리면 배포본끼리 덮어쓴다)

# 배포본에 들어가는 00~05. 06은 원본 전용이라 제외한다.




def write_bat_file(path: Path, content: str):
    """Write batch file encoded in CP949 with CRLF line endings for native Windows cmd.exe compatibility."""
    validate_batch_file_rules(content, path.name)
    crlf = content.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(crlf.encode('cp949'))
    print(f"[OK] 배치파일 무결성 검증 및 작성 완료 (CP949, CRLF): {path.name} -> {path.parent.name}")


def main():
    print("=" * 65)
    print("국회·대외기관 자료요구 스마트시스템 배치파일 일괄 생성 (CP949/CRLF)")
    print("=" * 65)

    in_distribution = system_config.is_distribution_dir(SYSTEM_DIR)

    # 1. System Directory batch files
    print(f"\n[1] 프로그램 폴더 배치파일 생성: {SYSTEM_DIR.name}")
    for name, content in DISTRIBUTED_BATS:
        write_bat_file(SYSTEM_DIR / name, content)
    if not in_distribution:
        for name, content in SOURCE_ONLY_BATS:
            write_bat_file(SYSTEM_DIR / name, content)

    # 2. Keep WORKSPACE_ROOT clean (do not place scripts/batch files in root folder)
    if WORKSPACE_ROOT != SYSTEM_DIR:
        for bat_name, _ in DISTRIBUTED_BATS + SOURCE_ONLY_BATS:
            root_bat = WORKSPACE_ROOT / bat_name
            if root_bat.exists():
                try:
                    root_bat.unlink()
                    print(f"✓ 루트 폴더 정리 (배치파일 제거): {bat_name}")
                except Exception:
                    pass

    if in_distribution:
        return

    # 3. Distribution Directory (if exists)
    if DIST_DIR.exists():
        print(f"\n[3] 배포 폴더 배치파일 생성: {DIST_DIR.name}")
        for name, content in DISTRIBUTED_BATS:
            write_bat_file(DIST_DIR / name, content)

    # 4. Universal Distribution Directory (if exists)
    if UNIVERSAL_DIST_DIR.exists():
        print(f"\n[4] 타 부서 클린 배포 폴더 배치파일 생성: {UNIVERSAL_DIST_DIR.name}")
        for name, content in DISTRIBUTED_BATS:
            write_bat_file(UNIVERSAL_DIST_DIR / name, content)

    print("\n" + "=" * 65)
    print("모든 배치파일 작성이 완료되었습니다.")
    print("=" * 65)


if __name__ == '__main__':
    main()
