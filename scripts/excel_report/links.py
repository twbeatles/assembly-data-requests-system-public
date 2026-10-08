# -*- coding: utf-8 -*-
"""엑셀 하이퍼링크 상대경로(SRP: 링크 계산)."""
import os


def resolve_excel_hyperlink_path(rel_path: str) -> str:
    """Calculates safe relative path from the Excel file location to the target file for clickable HYPERLINK."""
    import generate_excel_db as ge  # 지연 import: 진입점 전역 패치를 그대로 본다.
    if not rel_path:
        return ""
    clean = str(rel_path).replace('\\', '/').lstrip('/')
    target_abs = (ge.ROOT_DIR / clean).resolve()
    try:
        rel_from_excel = os.path.relpath(target_abs, start=ge.EXCEL_PATH.parent)
        return str(rel_from_excel).replace('/', '\\')
    except (ValueError, OSError):
        return str(target_abs)
