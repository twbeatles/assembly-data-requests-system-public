# -*- coding: utf-8 -*-
"""
Master Distribution Synchronization Script (SOLID: Orchestrator).
Synchronizes all updates from the source project to:
 1. 배포용_자료요구_통합검색시스템 (부서 전용 배포 패키지)
 2. ../국회자료요구_스마트시스템_범용배포용 (타 부서 전달용 범용 클린 패키지)

Ensures all refactored modular packages (db, extractors, services, templates, core),
executables, batch files, and documentation stay 100% in sync with strict SHA256 parity verification.
"""

from typing import Any, cast
import os
import sys
import time
import hashlib
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
PARENT_DIR = SYSTEM_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import package_distribution
import build_universal_package
import write_all_bats
from template_renderer import DashboardRenderer

def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def verify_distribution(dist_path: Path, name: str) -> bool:
    print(f"\n🔍 [{name}] 구조 및 필수 파일 검증 중: {dist_path.name}")
    required_dirs = [
        "scripts",
        "scripts/db",
        "scripts/extractors",
        "scripts/extractors/ledger",
        "scripts/services",
        "scripts/services/excel_sync",
        "scripts/services/ledger",
        "scripts/templates/dashboard",
        "scripts/web",
        "scripts/gui",
        "scripts/pipeline/parse",
        "새자료_투입폴더",
    ]
    required_files = [
        "00_새자료_추가_및_DB동기화.bat",
        "01_웹대시보드_실행.bat",
        "02_웹관리서버_실행.bat",
        "03_엑셀DB_열기.bat",
        "04_부서명_간편설정.bat",
        "05_통합검색프로그램_실행.bat",
        "07_기존자료_안전마이그레이션.bat",
        "config.json",
        "자료요구_통합검색_대시보드.html"
    ]

    all_ok = True
    for d in required_dirs:
        p = dist_path / d
        if not p.exists() or not p.is_dir():
            print(f"  ❌ 필수 디렉토리 누락: {d}")
            all_ok = False
        else:
            print(f"  ✓ 디렉토리 확인: {d}")

    for f in required_files:
        p = dist_path / f
        if not p.exists():
            print(f"  ❌ 필수 파일 누락: {f}")
            all_ok = False
        else:
            print(f"  ✓ 파일 확인: {f} ({p.stat().st_size:,} B)")

    dashboard_path = dist_path / "자료요구_통합검색_대시보드.html"
    if dashboard_path.exists():
        offline_ok, offline_error = DashboardRenderer.verify_offline_dashboard_html(
            dashboard_path.read_text(encoding="utf-8", errors="replace")
        )
        if offline_ok:
            print("  ✓ HTML 단독 배포 검증: 내장 데이터·전문·스크립트 독립성 확인")
        else:
            print(f"  ❌ HTML 단독 배포 검증 실패: {offline_error}")
            all_ok = False

    if (dist_path / "자료요구_통합검색.exe").exists():
        exe_size = (dist_path / "자료요구_통합검색.exe").stat().st_size
        print(f"  ✓ 독립 실행 파일 확인: 자료요구_통합검색.exe ({exe_size:,} B)")

    return all_ok

def verify_strict_code_parity(dept_path: Path, univ_path: Path) -> bool:
    print("\n🔬 [정밀 검증] 원본 vs 부서배포용 vs 범용배포용 SHA256 코드 동일성 대조")
    src_scripts = SCRIPTS_DIR
    dept_scripts = dept_path / "scripts"
    univ_scripts = univ_path / "scripts"

    src_files = []
    for root, dirs, files in os.walk(src_scripts):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(('.py', '.html', '.js', '.sql')):
                full = Path(root) / f
                rel = full.relative_to(src_scripts)
                src_files.append(rel)

    src_files.sort()
    mismatches = []
    for rel in src_files:
        p_src = src_scripts / rel
        p_dept = dept_scripts / rel
        p_univ = univ_scripts / rel

        h_src = sha256_file(p_src)
        if not p_dept.exists() or sha256_file(p_dept) != h_src:
            mismatches.append((str(rel), "부서배포본 불일치/누락"))
        if not p_univ.exists() or sha256_file(p_univ) != h_src:
            mismatches.append((str(rel), "범용배포본 불일치/누락"))

    # Verify EXE parity
    exe_src = SYSTEM_DIR / "자료요구_통합검색.exe"
    exe_dept = dept_path / "자료요구_통합검색.exe"
    exe_univ = univ_path / "자료요구_통합검색.exe"
    if exe_src.exists() and exe_dept.exists() and exe_univ.exists():
        h_exe = sha256_file(exe_src)
        if sha256_file(exe_dept) != h_exe or sha256_file(exe_univ) != h_exe:
            mismatches.append(("자료요구_통합검색.exe", "실행 파일 해시 불일치"))

    # Verify every distributed batch, including non-numeric additions such as 07.
    for bname, _content in write_all_bats.DISTRIBUTED_BATS:
        b_src = SYSTEM_DIR / bname
        b_dept = dept_path / bname
        b_univ = univ_path / bname
        h_b = sha256_file(b_src)
        if not b_dept.exists() or sha256_file(b_dept) != h_b:
            mismatches.append((b_src.name, "부서배포본 배치파일 해시 불일치/누락"))
        if not b_univ.exists() or sha256_file(b_univ) != h_b:
            mismatches.append((b_src.name, "범용배포본 배치파일 해시 불일치/누락"))

    if not mismatches:
        print(f"  ✓ 스크립트 {len(src_files)}종 및 핵심 리소스 100% SHA256 무결성 일치 확인 완료")
        return True
    else:
        print(f"  ❌ 불일치 항목 {len(mismatches)}건 발생:")
        for item, reason in mismatches:
            print(f"     - {item}: {reason}")
        return False

def sync_all():
    start_t = time.time()
    print("=" * 65)
    print("🔄 [통합 동기화] 원본 프로젝트 -> 배포용 패키지 동기화 시작")
    print("=" * 65)
    print(f"📍 원본 프로젝트: {SYSTEM_DIR.name}")

    dept_dist_path = SYSTEM_DIR / "배포용_자료요구_통합검색시스템"
    univ_dist_path = PARENT_DIR / "국회자료요구_스마트시스템_범용배포용"

    # Execute package distribution (orchestrates both department and universal packages)
    package_distribution.main()

    # Structural verification
    ok_dept = verify_distribution(dept_dist_path, "부서 전용 배포 패키지")
    ok_univ = verify_distribution(univ_dist_path, "타 부서 범용 클린 패키지")

    # Strict SHA256 code parity verification
    ok_parity = verify_strict_code_parity(dept_dist_path, univ_dist_path)

    elapsed = time.time() - start_t
    print("\n" + "=" * 65)
    if ok_dept and ok_univ and ok_parity:
        print(f"🎉 모든 배포용 패키지 동기화 및 100% 코드 동일성 검증 완료! (소요시간: {elapsed:.2f}초)")
        print(f" 1. [부서 전용] {dept_dist_path}")
        print(f" 2. [타부서용]  {univ_dist_path}")
    else:
        print("⚠️ 일부 항목에 불일치 또는 누락이 발견되었습니다. 상단 검증 로그를 확인하세요.")
    print("=" * 65)
    return ok_dept and ok_univ and ok_parity

if __name__ == "__main__":
    success = sync_all()
    sys.exit(0 if success else 1)
