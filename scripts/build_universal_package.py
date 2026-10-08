from typing import Any, Optional, cast
import os
import sys
import json
import shutil
import sqlite3
import gzip
import base64
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_config

# 루트 판정은 system_config 한 곳에서만 한다. 예전에는 "2026" 폴더 유무로 판정해, 연도가 바뀌면
# 범용 배포본이 팀 워크스페이스가 아니라 시스템 폴더 안에 만들어졌다. (AGENTS.md 불변 조건 12)
WORKSPACE_ROOT = system_config.find_workspace_root(SYSTEM_DIR)
import create_ledger_template
import write_all_bats
from universal_pkg.content import HTML_MANUAL_CONTENT_UNIVERSAL, README_TXT_CONTENT_UNIVERSAL
from universal_pkg.database import build_clean_database




def build_universal_package(target_dir: Optional[Path] = None) -> Path:
    if target_dir is None:
        target_dir = WORKSPACE_ROOT / "국회자료요구_스마트시스템_범용배포용"

    print("=" * 65)
    print(f"📦 타 부서 전용 클린 배포 패키지 구축 시작")
    print(f"📍 대상 폴더: {target_dir}")
    print("=" * 65)

    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Clean any legacy excel db or temp files in distribution folder
        for le in target_dir.glob("*통합DB*.xlsx"):
            try:
                le.unlink()
                print(f"✓ 배포용 폴더 내 엑셀 DB 제외 조치: {le.name} 삭제")
            except Exception:
                pass

    # 1. Scripts copy (including subpackages db, extractors, services, templates)
    scripts_src = SYSTEM_DIR / "scripts"
    scripts_dst = target_dir / "scripts"
    scripts_dst.mkdir(exist_ok=True)
    for item in scripts_src.iterdir():
        if item.name == "__pycache__" or item.name.startswith("."):
            continue
        dst_item = scripts_dst / item.name
        if item.is_dir():
            shutil.copytree(item, dst_item, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(item, dst_item)
    print("✓ 범용 스크립트 및 모듈 서브패키지 동기화 완료")

    # 2. Config.json (Universal default)
    univ_config = {
        "department_name": "자료요구담당부서",
        "agency_name": "공공기관",
        "system_title": "국회·기관 자료요구 스마트 관리 및 검색 시스템",
        "system_subtitle": "공문서 답변 전문 & 지능형 검색·공유 협업 플랫폼",
        "excel_filename": "국회_기관_자료요구_통합DB.xlsx",
        "dashboard_filename": "자료요구_통합검색_대시보드.html",
        "default_contact": "",
        "ledger_patterns": [
            "*요구자료*목록*.xls*",
            "*자료요구*목록*.xls*",
            "*요구자료*대장*.xls*",
            "*관리대장*.xls*"
        ]
    }
    with open(target_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(univ_config, f, ensure_ascii=False, indent=2)
    print("✓ 타 부서 기본 config.json 생성 완료")

    # 폴더 이름을 바꿔도 배포본으로 인식되도록 표식을 남긴다. 데이터는 포함하지 않으므로
    # 받는 부서에서는 자체 대장·원문으로 SQLite·통합 엑셀·대시보드를 모두 만든다.
    (target_dir / ".distribution_manifest.json").write_text(
        json.dumps({"package": "universal", "data_included": False, "source_documents_included": False},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3. Batch files
    write_all_bats.write_bat_file(target_dir / "00_새자료_추가_및_DB동기화.bat", write_all_bats.SYSTEM_BAT_00)
    write_all_bats.write_bat_file(target_dir / "01_웹대시보드_실행.bat", write_all_bats.SYSTEM_BAT_01)
    write_all_bats.write_bat_file(target_dir / "02_웹관리서버_실행.bat", write_all_bats.SYSTEM_BAT_02)
    write_all_bats.write_bat_file(target_dir / "03_엑셀DB_열기.bat", write_all_bats.SYSTEM_BAT_03)
    write_all_bats.write_bat_file(target_dir / "04_부서명_간편설정.bat", write_all_bats.SYSTEM_BAT_04)
    write_all_bats.write_bat_file(target_dir / "05_통합검색프로그램_실행.bat", write_all_bats.SYSTEM_BAT_05)
    write_all_bats.write_bat_file(target_dir / "07_기존자료_안전마이그레이션.bat", write_all_bats.SYSTEM_BAT_07)
    print("✓ 원클릭 바로가기 배치파일 7종 작성 완료")

    exe_src = SYSTEM_DIR / "dist" / "자료요구_통합검색.exe"
    if exe_src.exists():
        shutil.copy2(exe_src, target_dir / "자료요구_통합검색.exe")
        print("✓ 독립 실행 파일(자료요구_통합검색.exe) 복사 완료")

    # 4. Excel Ledger Template
    template_src = SYSTEM_DIR / "(양식)국회_요구자료_목록_대장_템플릿.xlsx"
    template_dst = target_dir / "(양식)국회_요구자료_목록_대장_템플릿.xlsx"
    if template_src.exists():
        shutil.copy2(template_src, template_dst)
    else:
        # Building a universal package from a clean source checkout must not
        # create an ignored runtime artifact in the repository root.
        create_ledger_template.create_template(template_dst)
    print("✓ 국회 요구자료 목록 대장 템플릿 복사 완료")

    # 5. Drop folder & markdown dir
    drop_folder = target_dir / "새자료_투입폴더"
    drop_folder.mkdir(exist_ok=True)
    readme_drop = drop_folder / "README_여기에_파일을_넣으세요.txt"
    readme_drop_content = """================================================================================
[안내] 신규 공문서 및 관리대장 투입 안내
================================================================================
1. 이 폴더에 부서에서 보관 중인 공문서(HWP, HWPX, PDF) 파일이나
   국회 요구자료 목록 엑셀 파일(.xlsx, .xlsm)을 자유롭게 넣으세요.
2. 파일 투입 후, 상위 폴더의 '00_새자료_추가_및_DB동기화.bat'을 더블클릭하시면
   자동으로 연도별 분류, 텍스트 파싱, SQLite DB 및 웹 대시보드가 생성됩니다.
================================================================================
"""
    readme_drop.write_text(readme_drop_content, encoding="utf-8-sig")

    # 6. KorDoc Installer & Parsing Guide
    for msi_name in ["KorDoc.AI_1.5.1_x64_ko-KR.msi"]:
        msi_src = SYSTEM_DIR / msi_name
        if not msi_src.exists():
            msi_src = WORKSPACE_ROOT / msi_name
        if msi_src.exists() and not (target_dir / msi_name).exists():
            shutil.copy2(msi_src, target_dir / msi_name)
            print(f"✓ KorDoc 설치 파일 복사 완료: {msi_name}")

    guide_src = SYSTEM_DIR / "kordoc_파싱_및_설치_가이드.md"
    if guide_src.exists():
        shutil.copy2(guide_src, target_dir / "kordoc_파싱_및_설치_가이드.md")
        print("✓ KorDoc 파싱 및 설치 가이드 복사 완료")

    bat_guide_src = SYSTEM_DIR / "배치파일_안전설계_및_운영가이드.md"
    if bat_guide_src.exists():
        shutil.copy2(bat_guide_src, target_dir / "배치파일_안전설계_및_운영가이드.md")
        print("✓ 배치파일 안전설계 및 운영 가이드 복사 완료")

    # 7. Manuals
    (target_dir / "사용설명서_및_안내.html").write_text(HTML_MANUAL_CONTENT_UNIVERSAL, encoding="utf-8")
    (target_dir / "README_배포안내.txt").write_text(README_TXT_CONTENT_UNIVERSAL, encoding="utf-8-sig")
    print("✓ 사용설명서 HTML 및 README 텍스트 작성 완료")

    # 8. Clean Database & Dashboard
    build_clean_database(target_dir)
    print("✓ 깨끗하게 초기화된 독립 DB 및 웹 대시보드 생성 완료")

    print("\n" + "=" * 65)
    print(f"🎉 타 부서 전용 클린 배포 패키지 구축 성공!")
    print(f"📍 폴더 위치: {target_dir}")
    print(f"💡 이 폴더를 ZIP으로 압축하여 타 부서에 전달하시면 됩니다.")
    print("=" * 65)
    return target_dir

if __name__ == "__main__":
    build_universal_package()
