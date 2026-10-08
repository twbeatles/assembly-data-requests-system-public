# -*- coding: utf-8 -*-
"""_parsed_markdown 배치를 연도별 정본 규칙에 맞추는 수동 도구.

00번 파이프라인(`parse_all.main`)이 매번 같은 정리를 자동으로 하므로 보통은 실행할 필요가 없다.
파이프라인을 돌리기 전에 결과만 미리 보거나(`--dry-run`) 즉시 정리하고 싶을 때 쓴다.

배치 규칙은 `pipeline/parse/layout.py` 한 곳에 있다. 예전 이 도구는 파서와 다른 규칙으로
목적지를 계산해(원문 파일명 검색, '미분류' 평탄화), 정리 후에도 같은 원문의 마크다운이
두 자리에 생겨 DB 문서가 중복됐다. (감사 7회차 ISSUE-004)

사용법:
    python scripts/reorganize_markdown.py --dry-run   # 옮길·정리할 목록만 출력
    python scripts/reorganize_markdown.py             # 실행 (치운 사본은 .maintenance_backup/에 보관)
"""
from typing import Any, cast
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    cast(Any, sys.stdout).reconfigure(encoding='utf-8')
except Exception:
    pass

import parse_all
from pipeline_guard import PipelineGuard
from write_guard import ensure_writable

ROOT_DIR = parse_all.ROOT_DIR
PARSED_DIR = parse_all.OUTPUT_DIR


def reorganize_parsed_markdown(dry_run: bool = False) -> dict:
    """정본 규칙에 맞게 파싱 마크다운을 옮기고 중복 사본을 치운다."""
    print("=" * 60)
    print("📁 _parsed_markdown 연도별 배치 정리")
    print(f"원문 기준 경로: {ROOT_DIR}")
    print(f"마크다운 경로: {PARSED_DIR}")
    print(f"드라이 런(모의 실행): {'예' if dry_run else '아니오'}")
    print("=" * 60)

    if not PARSED_DIR.exists():
        print("✗ _parsed_markdown 폴더가 존재하지 않습니다.")
        return {"success": False, "moved": 0, "removed": 0}

    guard = None
    owned = False
    if not dry_run:
        ensure_writable(PARSED_DIR, "파싱 마크다운")
        guard = PipelineGuard(SYSTEM_DIR)
        try:
            owned = guard.acquire(allow_reentrant=False)
        except RuntimeError as e:
            print(f"✗ {e}")
            return {"success": False, "moved": 0, "removed": 0, "error": str(e)}
    try:
        files = parse_all.find_target_files(ROOT_DIR)
        if not files:
            print("✗ 원문 문서를 한 건도 찾지 못해 정리하지 않습니다. 기준 경로를 확인하세요.")
            return {"success": False, "moved": 0, "removed": 0}
        result = parse_all.consolidate_markdown_layout(files, dry_run=dry_run)
    finally:
        if guard is not None and owned:
            guard.release()

    for line in result["moved"]:
        print(f"  이동: {line}")
    for line in result["removed"]:
        print(f"  중복 정리: {line}")
    print("=" * 60)
    verb = "예정" if dry_run else "완료"
    print(f"✓ 정리 {verb}: 이동 {len(result['moved'])}건, 중복 정리 {len(result['removed'])}건")
    if not dry_run and result["removed"]:
        print("  치운 사본은 .maintenance_backup/markdown_layout_<시각>/ 에 보관했습니다.")
    print("=" * 60)
    return {"success": True, "moved": len(result["moved"]), "removed": len(result["removed"])}


if __name__ == "__main__":
    res = reorganize_parsed_markdown(dry_run="--dry-run" in sys.argv)
    sys.exit(0 if res.get("success") else 1)
