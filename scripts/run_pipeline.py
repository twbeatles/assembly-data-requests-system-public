from typing import Any, cast
import os
import sys
import time
import subprocess
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPTS_DIR.parent

def run_step(step_num, title, script_name):
    print("\n" + "=" * 70)
    print(f"[단계 {step_num}] {title} ({script_name})")
    print("=" * 70)
    t0 = time.time()
    script_path = SCRIPTS_DIR / script_name
    res = subprocess.run([sys.executable, "-u", str(script_path)], text=True, encoding='utf-8', errors='replace')
    elapsed = time.time() - t0
    if res.returncode != 0:
        print(f"경고: {script_name} 실행 중 오류 발생 (종료 코드 {res.returncode})")
        return False
    else:
        print(f"단계 {step_num} 완료 ({elapsed:.2f}초 소요)")
        return True

def main():
    print("*" * 70)
    print("기획예산팀 국회·대외기관 자료요구 문서 통합 DB 자동 구축 파이프라인")
    print("*" * 70)
    total_start = time.time()

    # Step 0: Ingest drop folder documents if any
    try:
        import add_documents_smart
        for dd in add_documents_smart.DROP_DIRS:
            if dd.exists():
                drops = [f for f in dd.iterdir() if f.is_file() and f.suffix.lower() in add_documents_smart.TARGET_EXTS]
                if drops:
                    print(f"\n[사전 단계] 투입 폴더({dd.name}) 신규 문서 {len(drops)}개 감지 및 연도별 자동 분류 배치...")
                    add_documents_smart.copy_or_move_files(drops, move=True)
    except Exception as ex:
        print(f"사전 투입 문서 처리 알림: {ex}")

    steps = [
        (1, "kordoc 일괄 병렬 파싱", "parse_all.py"),
        (2, "문서 메타데이터 추출 및 표준 DB 구축", "extract_and_build_db.py"),
        (3, "사용자 공유용 엑셀 통합 DB 생성", "generate_excel_db.py"),
        (4, "무설치 웹 실시간 검색 대시보드(HTML) 생성", "generate_web_dashboard.py"),
        (5, "타인 배포용 패키지 폴더 동기화", "package_distribution.py"),
    ]
    for step_num, title, script_name in steps:
        if not run_step(step_num, title, script_name):
            print(f"✗ 단계 {step_num} 실패: 이후 산출물 생성을 중단합니다.")
            return 1

    total_elapsed = time.time() - total_start
    print("\n" + "*" * 70)
    print(f"🎉 모든 파이프라인 구축 완료! (총 소요시간: {total_elapsed:.2f}초)")
    print("*" * 70)
    print("생성된 주요 산출물 (상위 루트 폴더 위치):")
    print(" 1. 전담팀 배포 패키지: 배포용_자료요구_통합검색시스템/ (기존 데이터 포함)")
    print(" 2. 타 부서 전용 클린 패키지: 국회자료요구_스마트시스템_범용배포용/ (타 부서 자체 DB 적재용)")
    print(" 3. 엑셀 통합 데이터베이스: 국회_대외기관_자료요구_통합DB.xlsx")
    print(" 4. 웹 실시간 검색 대시보드: 자료요구_통합검색_대시보드.html")
    print(" 5. SQLite 표준 데이터베이스: data_requests.db")
    print(" 6. JSON 통합 데이터: data_requests.json")
    print(" 7. 파싱 마크다운 저장소: _parsed_markdown/")
    print(" 8. 파이프라인 스크립트 모음: scripts/")
    print("*" * 70)
    return 0

if __name__ == "__main__":
    sys.exit(main())
