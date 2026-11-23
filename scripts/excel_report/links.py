# -*- coding: utf-8 -*-
"""엑셀 하이퍼링크 상대경로(SRP: 링크 계산)."""

import os


def hyperlink_literal(text: str) -> str:
    """HYPERLINK 수식 안에 넣을 표시·경로 문자열. `"`를 2배화한다.

    Windows 파일명·대장ID에는 `"`가 올 수 없어 평소에는 그대로지만, 형식
    문자열 탈출을 막는 2중 장치다. (PROJECT_AUDIT_SECURITY.md SEC-001)
    """
    return str(text or "").replace('"', '""')


def resolve_excel_hyperlink_path(rel_path: str) -> str:
    """Calculates safe relative path from the Excel file location to the target file for clickable HYPERLINK."""
    import generate_excel_db as ge  # 지연 import: 진입점 전역 패치를 그대로 본다.

    if not rel_path:
        return ""
    clean = str(rel_path).replace("\\", "/").lstrip("/")
    target_abs = (ge.ROOT_DIR / clean).resolve()
    try:
        rel_from_excel = os.path.relpath(target_abs, start=ge.EXCEL_PATH.parent)
        return str(rel_from_excel).replace("/", "\\")
    except (ValueError, OSError):
        return str(target_abs)
