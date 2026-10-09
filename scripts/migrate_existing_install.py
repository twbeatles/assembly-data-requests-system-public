# -*- coding: utf-8 -*-
"""Safely import parsed Markdown from a prior installed package.

The prior package is treated as read-only.  This tool never deletes or
overwrites data: identical files are skipped and same-name, different-content
files are retained under a deterministic ``__migrated_N`` filename.
"""

import argparse
import glob
import hashlib
import shutil
import os
import tempfile
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def conflict_destination(destination: Path) -> Path:
    """Return an unused sibling path without replacing an existing document."""
    for number in range(1, 10_000):
        candidate = destination.with_name(
            f"{destination.stem}__migrated_{number}{destination.suffix}"
        )
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"동일 이름 충돌 파일이 너무 많습니다: {destination.name}")


def migrate_markdown(source_system_dir: Path, destination_system_dir: Path = SYSTEM_DIR) -> dict:
    from pipeline_guard import PipelineGuard
    from db.preflight import check_database_version
    from write_guard import ensure_writable
    destination_system_dir = Path(destination_system_dir)
    if Path(source_system_dir).resolve() == destination_system_dir.resolve():
        raise ValueError("현재 프로그램 폴더는 이전 설치본으로 지정할 수 없습니다.")
    ensure_writable(destination_system_dir / ".pipeline.lock", "마크다운 이전")
    guard = PipelineGuard(destination_system_dir)
    owned = guard.acquire()
    try:
        check_database_version(destination_system_dir / "data_requests.db")
        return _migrate_markdown_locked(source_system_dir, destination_system_dir)
    finally:
        if owned:
            guard.release()


def _migrate_markdown_locked(source_system_dir: Path, destination_system_dir: Path) -> dict:
    """Copy a previous installation's Markdown cache without changing its files."""
    source_system_dir = Path(source_system_dir).resolve()
    destination_system_dir = Path(destination_system_dir).resolve()
    if source_system_dir == destination_system_dir:
        raise ValueError("현재 프로그램 폴더는 이전 설치본으로 지정할 수 없습니다.")

    source_root = source_system_dir / "_parsed_markdown"
    if not source_root.is_dir():
        raise FileNotFoundError(f"이전 설치본에 _parsed_markdown 폴더가 없습니다: {source_system_dir}")

    destination_root = destination_system_dir / "_parsed_markdown"
    files = sorted(path for path in source_root.rglob("*.md") if path.is_file())
    copied = skipped = renamed = 0

    for source in files:
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        if destination.exists():
            if sha256_file(source) == sha256_file(destination):
                skipped += 1
                continue
            source_hash = sha256_file(source)
            # 파일명의 `[`·`]`가 glob 문자집합으로 해석되지 않게 이스케이프한다.
            # 그렇지 않으면 기존 충돌 사본을 못 찾아 매 실행마다 사본이 하나씩 는다.
            # (2026-09-28 감사 ISSUE-001)
            siblings = destination.parent.glob(
                glob.escape(destination.stem) + "__migrated_*" + glob.escape(destination.suffix)
            )
            if any(sha256_file(candidate) == source_hash for candidate in siblings if candidate.is_file()):
                skipped += 1
                continue
            destination = conflict_destination(destination)
            renamed += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        from write_guard import ensure_writable
        ensure_writable(destination, "이전 마크다운")
        fd, staging = tempfile.mkstemp(prefix=".migration-", dir=destination.parent)
        os.close(fd)
        try:
            shutil.copy2(source, staging)
            os.replace(staging, destination)
        finally:
            Path(staging).unlink(missing_ok=True)
        copied += 1

    return {
        "source": source_system_dir,
        "found": len(files),
        "copied": copied,
        "skipped": skipped,
        "renamed": renamed,
        "destination": destination_root,
    }


def rebuild_outputs() -> bool:
    """Recreate every derived artifact only after the non-destructive copy succeeds."""
    import extract_and_build_db
    import generate_excel_db
    import generate_web_dashboard

    result = extract_and_build_db.main()
    if not result or not result.get("success"):
        print(f"✗ SQLite/JSON 재구축 실패: {result.get('error', '알 수 없는 오류') if result else ''}")
        return False
    if not generate_excel_db.main():
        print("✗ 통합 엑셀 재구축 실패")
        return False
    dashboard_result = generate_web_dashboard.main()
    if not dashboard_result or not dashboard_result.get("success"):
        print("✗ 웹 대시보드 재구축 실패")
        return False
    return True


def choose_previous_folder() -> Optional[Path]:
    """Open a normal folder picker so users never need to type a path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=str(SYSTEM_DIR.parent),
                title="이전 버전의 프로그램 폴더를 선택하세요",
            )
        finally:
            root.destroy()
        return Path(selected) if selected else None
    except Exception as error:
        print(f"폴더 선택 창을 열 수 없습니다: {error}")
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="이전 설치본의 파싱 마크다운 안전 이전")
    parser.add_argument("previous_folder", nargs="?", help="이전 프로그램 폴더 경로")
    args = parser.parse_args(argv)
    previous_folder = Path(args.previous_folder) if args.previous_folder else choose_previous_folder()
    if previous_folder is None:
        print("마이그레이션을 취소했습니다. 변경된 파일은 없습니다.")
        return 2
    try:
        summary = migrate_markdown(previous_folder)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"✗ 마이그레이션을 시작하지 않았습니다: {error}")
        return 1

    print(
        "✓ 이전 마크다운 확인 {found}건 / 복사 {copied}건 / 동일 파일 건너뜀 {skipped}건 / "
        "이름 충돌 보존 {renamed}건".format(**summary)
    )
    if not rebuild_outputs():
        print("원본 설치본과 이전한 마크다운은 그대로 보존되었습니다. 오류를 해결한 뒤 같은 배치를 다시 실행하세요.")
        return 1
    print("✓ SQLite DB, 통합 엑셀, 웹 대시보드에 이전 마크다운을 반영했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
