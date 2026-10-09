# -*- coding: utf-8 -*-
"""타 부서용 클린 DB·대시보드 생성(SRP: 클린 패키지 DB)."""
import base64
import gzip
import json
import sqlite3
from pathlib import Path


def build_clean_database(dist_dir: Path, dept_name="자료요구담당부서", agency_name="공공기관"):
    """Creates a clean, initialized database for another department."""
    db_file = dist_dir / "data_requests.db"
    json_file = dist_dir / "data_requests.json"
    excel_file = dist_dir / "국회_기관_자료요구_통합DB.xlsx"
    html_file = dist_dir / "자료요구_통합검색_대시보드.html"

    # 1. Clean JSON
    clean_json_data = {
        "documents": [],
        "qa_items": [],
        "request_ledger": []
    }
    with open(json_file, "w", encoding="utf-8") as fp:
        json.dump(clean_json_data, fp, ensure_ascii=False, indent=2)

    # 2. Clean SQLite DB with schema
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass

    from db.database_manager import DatabaseManager
    conn = sqlite3.connect(str(db_file))
    try:
        DatabaseManager.init_schema(conn, drop_existing=True)
        conn.commit()
    finally:
        conn.close()

    # 3. Clean Dashboard HTML
    import generate_web_dashboard
    marked_js = ""
    marked_path = dist_dir / "scripts" / "marked.min.js"
    if marked_path.exists():
        with open(marked_path, "r", encoding="utf-8") as fp:
            marked_js = fp.read()

    payload = clean_json_data
    from template_renderer import DashboardRenderer
    b64_str, body_b64s = DashboardRenderer.encode_payload_parts(payload)

    sys_title = "국회·기관 자료요구 스마트 관리 및 검색 시스템"
    sys_subtitle = "공문서 답변 전문 & 지능형 검색·공유 협업 플랫폼"

    html_content = (
        DashboardRenderer.embed_data(generate_web_dashboard.HTML_TEMPLATE, b64_str, body_b64s)
        .replace("/* __APP_CONFIG_JSON__ */ null", json.dumps(
            {"department_name": dept_name, "agency_name": agency_name,
             "system_title": sys_title, "system_subtitle": sys_subtitle}, ensure_ascii=False).replace("</", "<\\/"))
        .replace("/* __MARKED_JS__ */", marked_js)
        .replace("__PAGE_TITLE__", f"{dept_name} {sys_title}")
        .replace("__HEADER_TITLE__", f"🛡️ {dept_name} {sys_title}")
        .replace("__HEADER_SUBTITLE__", f"{agency_name} {dept_name} | {sys_subtitle}")
        .replace("__DEFAULT_DEPT__", dept_name)
    )
    with open(html_file, "w", encoding="utf-8") as fp:
        fp.write(html_content)

    # Note: 배포용 폴더에서는 엑셀 DB에 적재/포함되지 않도록 조치
    legacy_excel1 = dist_dir / "국회_기관_자료요구_통합DB.xlsx"
    legacy_excel2 = dist_dir / "국회_대외기관_자료요구_통합DB.xlsx"
    for le in [legacy_excel1, legacy_excel2]:
        if le.exists():
            try:
                le.unlink()
            except Exception:
                pass
