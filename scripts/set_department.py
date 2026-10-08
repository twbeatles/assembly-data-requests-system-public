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

LABEL_RE = re.compile(r'^[가-힣A-Za-z0-9 ·.,()/\-]{1,80}$')
FORBIDDEN_SNIPPETS = ("__", "<", ">", "`", "script")


def validate_label(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    low = text.lower()
    if any(s in low for s in FORBIDDEN_SNIPPETS):
        raise ValueError(f"{field}에 허용되지 않은 문자가 있습니다.")
    if not LABEL_RE.match(text):
        raise ValueError(f"{field}은(는) 한글·영문·숫자와 공백·일부 기호만 80자 이내로 입력하세요.")
    return text

def main():
    print("=" * 65)
    print(" ⚙️  [국회·기관 자료요구 스마트시스템] 부서명 및 환경설정 마법사")
    print("=" * 65)

    cfg = system_config.get_config(SYSTEM_DIR)
    curr_dept = cfg.get("department_name", "자료요구담당부서")
    curr_agency = cfg.get("agency_name", "공공기관")
    curr_title = cfg.get("system_title", "국회·대외기관 자료요구 스마트 관리 및 검색 시스템")

    print(f"\n[현재 설정 정보]")
    print(f" 1. 담당 부서명 : {curr_dept}")
    print(f" 2. 소속 기관명 : {curr_agency}")
    print(f" 3. 시스템 제목 : {curr_title}")
    print("-" * 65)

    try:
        new_dept = validate_label(input("새로운 부서명을 입력하세요 (기존 유지 시 엔터): "), "부서명")
        new_agency = validate_label(input("새로운 기관명을 입력하세요 (기존 유지 시 엔터): "), "기관명")
        new_title = validate_label(input("새로운 시스템 제목을 입력하세요 (기존 유지 시 엔터): "), "시스템 제목")
    except ValueError as e:
        print(f"\n[오류] {e}")
        sys.exit(1)

    if new_dept:
        cfg["department_name"] = new_dept
    if new_agency:
        cfg["agency_name"] = new_agency
    if new_title:
        cfg["system_title"] = new_title

    ok = system_config.save_config(cfg, SYSTEM_DIR)
    if ok:
        print("\n" + "=" * 65)
        print("✓ 설정이 성공적으로 저장되었습니다!")
        print(f" • 부서명 : {cfg.get('department_name')}")
        print(f" • 기관명 : {cfg.get('agency_name')}")
        print(f" • 타이틀 : {cfg.get('system_title')}")
        print("=" * 65)
        
        # Re-generate web dashboard (and excel only if not in distribution environment)
        json_path = SYSTEM_DIR / "data_requests.json"
        if json_path.exists():
            print("\n웹 대시보드에 새 설정을 반영 중...")
            try:
                import generate_web_dashboard
                generate_web_dashboard.main()
            except Exception as e:
                print(f"대시보드 갱신 알림: {e}")
            
            is_dist_env = "배포" in SYSTEM_DIR.name or "dist" in SYSTEM_DIR.name.lower()
            if not is_dist_env:
                try:
                    import generate_excel_db
                    generate_excel_db.main()
                except Exception as e:
                    print(f"엑셀 DB 갱신 알림: {e}")
    else:
        print("\n[오류] 설정 저장에 실패했습니다.")
        sys.exit(1)

if __name__ == "__main__":
    main()
