from typing import Any, cast
import os
import sys
import re
import time
import datetime
import shutil
import hashlib
from pathlib import Path
import json

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import system_config
from write_guard import ensure_writable

# 배포 환경 판정과 루트 판정은 system_config 한 곳에서만 한다. 예전에는 여기서 폴더명의
# "배포"/"dist"로 따로 판정해 parse_all과 루트가 달라졌고, 투입 문서가 파싱 대상 밖으로
# 옮겨지거나 범용 배포본이 팀 원문을 파싱했다. (감사 R3-14, AGENTS.md 불변 조건 12)
IS_DIST_ENV = system_config.is_distribution_dir(SYSTEM_DIR)
# 원문 없이 데이터를 담아 보낸 부서 배포본만 통합 엑셀 DB 생성을 건너뛴다. 범용 배포본을
# 받은 타 부서는 자체 대장으로 5개 시트 통합 엑셀을 만들어야 한다. (감사 R3-04)
IS_DATA_DIST_ENV = system_config.is_data_distribution(SYSTEM_DIR)
WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
ROOT_DIR = WORKSPACE_ROOT
DROP_DIRS = list(dict.fromkeys([
    WORKSPACE_ROOT / "새자료_투입폴더", SYSTEM_DIR / "새자료_투입폴더", WORKSPACE_ROOT / "새자료_넣는곳"
]))

# `.xlsm` 대장도 투입 폴더에서 받는다(대장 판정 시 시스템 폴더로 옮김). 문서 파싱 대상 확장자는
# parse_all.TARGET_EXTENSIONS가 따로 정한다.

import parse_all
import extract_and_build_db
import generate_excel_db
import generate_web_dashboard
import package_distribution
import sync_distributions
from ingest.files import (
    TARGET_EXTS, is_master_ledger_file, determine_target_folder,
    _compute_sha256, _is_in_use, copy_or_move_files,
)







def _failure_result(error, elapsed_seconds):
    """실패 결과의 공통 필드. main은 이 계약을 믿고 elapsed를 직접 읽는다. (감사 ISSUE-004)"""
    return {
        "success": False,
        "newly_added_count": 0,
        "total_docs": 0,
        "total_qa": 0,
        "elapsed_seconds": elapsed_seconds,
        "warnings": [],
        "error": str(error),
    }


def run_smart_add(input_files=None, status_callback=None):
    from pipeline_guard import PipelineGuard
    t_start = time.time()
    guard = PipelineGuard(SYSTEM_DIR)
    owned_lock = False
    try:
        owned_lock = guard.acquire(allow_reentrant=True)
    except RuntimeError as e:
        print(f"[오류] {e}")
        return _failure_result(e, time.time() - t_start)
    try:
        from db.preflight import check_database_version
        check_database_version(extract_and_build_db.DB_PATH)
        return _run_smart_add_body(input_files, status_callback)
    except Exception as e:
        return _failure_result(e, time.time() - t_start)
    finally:
        if owned_lock:
            guard.release()


def _run_smart_add_body(input_files=None, status_callback=None):
    t_start = time.time()
    newly_added = []
    skipped_inputs = []
    ignored_inputs = []
    ignored_total = 0

    # Clean any stale staging markdown directory
    for dd in DROP_DIRS:
        staging_dir = SYSTEM_DIR / "_parsed_markdown" / dd.name
        if staging_dir.exists():
            try:
                shutil.rmtree(staging_dir, ignore_errors=True)
            except Exception:
                pass

    if status_callback:
        status_callback("파일 감지 및 배치 중...", 10)

    # 1. Process explicit files
    if input_files:
        print(f"지정된 신규 파일 {len(input_files)}개 처리 중...")
        newly_added = copy_or_move_files(input_files, move=False, skipped=skipped_inputs)

    # 2. Process drop folders (recursively scanning all nested subfolders)
    for dd in DROP_DIRS:
        if dd.exists():
            drop_files = []
            for root, dirs, files in os.walk(dd):
                for f in files:
                    if f.startswith('~$') or f.startswith('.'):
                        continue
                    fp = Path(root) / f
                    if not fp.is_file():
                        continue
                    if fp.suffix.lower() in TARGET_EXTS:
                        drop_files.append(fp)
                    else:
                        # 지원 외 형식은 조용히 버리지 않고 건너뜀 안내에 합친다. (감사 R6 정리)
                        ignored_total += 1
                        if len(ignored_inputs) < 3:
                            ignored_inputs.append(f)
            if drop_files:
                print(f"📁 전용 투입 폴더({dd.name}) 및 하위 폴더에서 신규 파일 {len(drop_files)}개 발견!")
                moved = copy_or_move_files(drop_files, move=True, skipped=skipped_inputs)
                newly_added.extend(moved)
                # Clean up empty subdirectories left behind inside dd
                for root, dirs, files in os.walk(dd, topdown=False):
                    for d in dirs:
                        sub_d = Path(root) / d
                        try:
                            if not any(sub_d.iterdir()):
                                sub_d.rmdir()
                        except Exception:
                            pass

    steps: list[tuple[str, int, Any]] = [
        ("1. kordoc 신규 문서 고속 증분 파싱", 25, parse_all.main),
        ("2. 메타데이터 추출 및 SQLite/JSON DB 갱신", 50, extract_and_build_db.main),
    ]

    # 데이터 포함 부서 배포본에서만 통합 엑셀 DB를 건너뛴다. 범용 배포본(타 부서 자체 구축)은 만든다.
    if not IS_DATA_DIST_ENV:
        steps.append(("3. 엑셀 통합 DB(5개 시트) 갱신", 75, generate_excel_db.main))
    else:
        print("\n💡 [배포용 환경 안내] 데이터 포함 배포본에서는 엑셀 DB 적재를 건너뛰고 웹/SQLite DB에 최적화하여 동기화합니다.")

    steps.append(("4. 웹 실시간 검색 대시보드(HTML) 재생성", 90, generate_web_dashboard.main))

    # Do not recursively create nested distribution packages inside distribution folders
    if not IS_DIST_ENV:
        steps.append(("5. 배포용 패키지 폴더 최신화 동기화", 98, sync_distributions.sync_all))

    pipeline_ok = True
    step_error = None
    warnings = []
    if skipped_inputs:
        names = ", ".join(name for name, _ in skipped_inputs[:3]) + (" 외" if len(skipped_inputs) > 3 else "")
        warnings.append(f"투입 파일 {len(skipped_inputs)}건을 사용 중이라 옮기지 못했습니다 ({names}). 파일을 닫고 00번을 다시 실행하세요")
    if ignored_total:
        names = ", ".join(ignored_inputs) + (" 외" if ignored_total > len(ignored_inputs) else "")
        warnings.append(f"투입 폴더의 {ignored_total}건은 지원하지 않는 형식이라 건너뛰었습니다 ({names}). HWP·HWPX·PDF·XLSX·XLSM만 투입됩니다")
    for desc, pct, func in steps:
        if status_callback:
            status_callback(desc, pct)
        print(f"\n[실행 중] {desc}...")
        t0 = time.time()
        result = func()
        step_success = result.get("success", True) if isinstance(result, dict) else result is not False
        if not step_success:
            pipeline_ok = False
            if isinstance(result, dict) and result.get("error"):
                step_error = f"{desc}: {result['error']}"
            print(f"✗ {desc} 검증 실패 ({time.time() - t0:.2f}초)")
            # A failed parser/build must not be followed by artifacts generated
            # from incomplete data or reported as a successful synchronization.
            break
        # 단계는 성공했지만 문서 일부가 실패한 경우. 나머지 산출물 생성은 계속하되
        # 사용자에게는 반드시 알린다. (문서 1건 실패로 전체를 중단하지 않는다.)
        if isinstance(result, dict) and result.get("partial_failures"):
            failed = result.get("failed_files") or []
            preview = ", ".join(failed[:3]) + (" 외" if len(failed) > 3 else "")
            warnings.append(
                f"{desc}: 문서 {result['partial_failures']}건 처리 실패"
                + (f" ({preview})" if preview else "")
            )
        print(f"✓ {desc} 완료 ({time.time() - t0:.2f}초)")

    if status_callback:
        status_callback("완료 처리 중...", 100)

    # Read latest stats from data_requests.json
    json_path = SYSTEM_DIR / "data_requests.json"
    doc_cnt = 0
    qa_cnt = 0
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                doc_cnt = len(data.get("documents", []))
                qa_cnt = len(data.get("qa_items", []))
        except Exception:
            pass

    try:
        import diagnose_data
        if json_path.exists():
            print("\n[데이터 진단 요약]")
            diagnose_data.main()
    except Exception:
        pass

    t_total = time.time() - t_start
    if warnings:
        print("\n⚠️ 일부 문서가 처리되지 않았습니다:")
        for w in warnings:
            print(f"   - {w}")
        print("   상세 원인은 parsing_report.json에서 확인하세요.")
    return {
        "success": pipeline_ok,
        "newly_added_count": len(newly_added),
        "total_docs": doc_cnt,
        "total_qa": qa_cnt,
        "elapsed_seconds": t_total,
        "warnings": warnings,
        "error": step_error,
    }

def main():
    print("=" * 65)
    print("🚀 국회·대외기관 요구자료 신규 문서 스마트 추가 및 DB 동기화")
    print("=" * 65)
    
    input_files = sys.argv[1:] if len(sys.argv) > 1 else None
    res = run_smart_add(input_files)

    print("\n" + "=" * 65)
    if not isinstance(res, dict):
        print("✗ 신규 요구자료 추가 및 DB 갱신 실패 (결과 형식이 올바르지 않습니다).")
        print("=" * 65)
        return 1
    elapsed = res.get("elapsed_seconds", 0.0) or 0.0
    if not res.get("success"):
        print(f"✗ 신규 요구자료 추가 및 DB 갱신 실패 (소요시간: {elapsed:.2f}초)")
        print(res.get("error") or "파이프라인 단계 실패로 기존 산출물을 유지했습니다.")
        print("=" * 65)
        return 1
    if res.get("warnings"):
        print(f"⚠️ 일부 문서 처리 실패를 제외하고 갱신을 완료했습니다. (소요시간: {elapsed:.2f}초)")
        for w in res.get("warnings") or []:
            print(f"   - {w}")
        print("=" * 65)
    print(f"🎉 신규 요구자료 추가 및 DB 갱신 성공! (총 소요시간: {elapsed:.2f}초)")
    print(f"📊 현재 데이터베이스 현황: 총 문서 {res.get('total_docs', 0)}건, 세부 질의(Q&A) {res.get('total_qa', 0):,}건")
    print("=" * 65)
    print("✨ 최신 반영된 산출물:")
    print(" 1. [웹 대시보드] 자료요구_통합검색_대시보드.html")
    if not IS_DATA_DIST_ENV:
        print(" 2. [엑셀 DB]     국회_대외기관_자료요구_통합DB.xlsx")
        print(" 3. [SQLite DB]   data_requests.db")
        print(" 4. [배포 패키지] 배포용_자료요구_통합검색시스템/")
    else:
        print(" 2. [SQLite DB]   data_requests.db")
    print("=" * 65)
    return 0

if __name__ == "__main__":
    sys.exit(main())
