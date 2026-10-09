from typing import Any, cast
import os
import sys
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent

def create_template(target_path: Path):
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    assert default_sheet is not None

    headers = [
        "연번", "접수일자", "소속", "의원", "보좌관",
        "요구자료", "세부내역", "마감일", "제출일", "제출여부", "요청형태", "비고",
        "담당부서", "대장ID"
    ]

    header_font = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=True)
    
    thin_border = Border(
        left=Side(style='thin', color="D3D3D3"),
        right=Side(style='thin', color="D3D3D3"),
        top=Side(style='thin', color="D3D3D3"),
        bottom=Side(style='thin', color="D3D3D3")
    )

    sample_row = [
        "1", "2026-09-01", "과방위", "홍길동", "이몽룡",
        "부서별 주요 현안 및 업무 추진실적",
        "1. 최근 3년간 주요 업무 추진 실적\n2. 예산 집행 현황 및 개선 대책\n3. 향후 중점 추진 계획",
        "2026-09-05", "2026-09-04", "제출", "시스템",
        "※ 샘플 예시입니다. 본인 부서의 요구자료로 수정하거나 삭제 후 행을 추가하세요.",
        "자료요구담당부서", "REQ-2026-001"
    ]

    import datetime
    this_year = datetime.datetime.now().year
    year_sheets = [str(y) for y in range(this_year, this_year - 9, -1)]
    sample_years = {"2026", "2025"}

    for year_sheet in year_sheets:
        ws = wb.create_sheet(title=year_sheet)
        ws.views.sheetView[0].showGridLines = True
        ws.row_dimensions[1].height = 28

        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        if year_sheet in sample_years:
            ws.row_dimensions[2].height = 45
            row_vals = list(sample_row)
            row_vals[-1] = f"REQ-{year_sheet}-001"
            for col_idx, val in enumerate(row_vals, start=1):
                cell = ws.cell(row=2, column=col_idx, value=val)
                cell.font = Font(name="맑은 고딕", size=9.5)
                cell.border = thin_border
                if col_idx in [1, 2, 3, 4, 5, 8, 9, 10, 11, 14]:
                    cell.alignment = center_align
                else:
                    cell.alignment = left_align

        # Auto column widths
        col_widths = {
            1: 8,   # 연번
            2: 13,  # 접수일자
            3: 12,  # 소속
            4: 12,  # 의원
            5: 12,  # 보좌관
            6: 35,  # 요구자료
            7: 50,  # 세부내역
            8: 13,  # 마감일
            9: 13,  # 제출일
            10: 10, # 제출여부
            11: 10, # 요청형태
            12: 30,  # 비고
            13: 16,  # 담당부서
            14: 16   # 대장ID
        }
        for col_idx, w in col_widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = w

    wb.remove(default_sheet)
    wb.save(str(target_path))
    print(f"✓ 관리대장 템플릿 생성 완료: {target_path.name}")

if __name__ == "__main__":
    out_file = SYSTEM_DIR / "(양식)국회_요구자료_목록_대장_템플릿.xlsx"
    create_template(out_file)
