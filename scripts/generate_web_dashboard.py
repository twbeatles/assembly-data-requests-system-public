# -*- coding: utf-8 -*-
"""
Web Search Dashboard Generator Module.
Refactored to follow Single Responsibility Principle (SRP).
Delegates data preparation, template substitution, and atomic persistence to DashboardRenderer.
Preserves HTML_TEMPLATE string in-file for unit test validation (e.g. XSS sanitizer tests).
"""

from typing import Any, cast
import sys
import time
from pathlib import Path

cast(Any, sys.stdout).reconfigure(encoding='utf-8')

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import system_config
from template_renderer import DashboardRenderer, DEFAULT_TEMPLATE_PATH

JSON_PATH = SYSTEM_DIR / "data_requests.json"
MARKED_PATH = SCRIPTS_DIR / "marked.min.js"

_renderer = DashboardRenderer(DEFAULT_TEMPLATE_PATH)

def load_template() -> str:
    """대시보드 소스 파트를 단일 HTML로 조립한다. 전달용은 항상 한 파일이다."""
    return _renderer.load_template()

# Module-level variable for backwards compatibility with existing unit tests
try:
    HTML_TEMPLATE = _renderer.load_template()
except Exception:
    HTML_TEMPLATE = ""
def load_history_for_dashboard(db_path) -> dict:
    """대장 변경 이력 타임라인 {ledger_id: [...]}. DB가 없거나 읽지 못하면 빈 dict.

    이력은 보조 정보라 실패해도 대시보드 생성을 막지 않는다.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    try:
        from extractors.ledger.workbook import connect_readonly
        from services.ledger.history import load_ledger_timelines
        conn = connect_readonly(db_path)
        try:
            return load_ledger_timelines(conn)
        finally:
            conn.close()
    except Exception as e:
        print(f"알림: 대장 변경 이력을 싣지 못했습니다(대시보드는 계속 생성): {e}")
        return {}


def main():
    print("=" * 60)
    print("추가 고도화 웹 대시보드 HTML 생성 (동의어 확장, 통계갤러리, 차트, 비교뷰어)")
    print("=" * 60)

    if not JSON_PATH.exists():
        print(f"오류: {JSON_PATH} 파일이 없습니다.")
        return {"success": False, "error": f"{JSON_PATH.name} 파일이 없습니다."}

    marked_js = ""
    if MARKED_PATH.exists():
        with open(MARKED_PATH, "r", encoding="utf-8") as fp:
            marked_js = fp.read()
        print(f"marked.js 로드 완료 ({len(marked_js):,} bytes)")
    else:
        print("경고: marked.min.js 파일이 없습니다.")

    import json
    with open(JSON_PATH, "r", encoding="utf-8") as fp:
        raw_data = json.load(fp)

    # 이력은 JSON 캐시와 같은 폴더의 DB에서 읽는다(JSON과 DB는 늘 한 쌍이다).
    raw_data["ledger_history"] = load_history_for_dashboard(JSON_PATH.parent / "data_requests.db")

    # 전문 보존: build_slim_payload는 중복 필드만 정리하고 본문은 자르지 않는다.
    slim_payload = _renderer.build_slim_payload(raw_data)
    del raw_data
    # 전문이 크면 본문을 여러 블록으로 나눠 싣는다(여전히 HTML 한 파일, 전문 전부 포함).
    b64_str, body_b64s = _renderer.encode_payload_parts(slim_payload)
    del slim_payload
    if body_b64s:
        print(f"문서 전문을 {len(body_b64s)}개 블록으로 나눠 내장합니다 (대용량 대시보드 모드)")

    curr_cfg = system_config.get_config(SYSTEM_DIR)
    html_content = _renderer.render(load_template(), marked_js, b64_str, curr_cfg, body_b64s)

    out_path = SYSTEM_DIR / curr_cfg.get("dashboard_filename", "자료요구_통합검색_대시보드.html")
    saved = _renderer.save_atomic(out_path, html_content)

    if saved:
        print(f"추가 고도화 웹 대시보드 생성 완료: {out_path.name} ({out_path.stat().st_size:,} bytes)")
    else:
        print(f"오류: 대시보드 저장 실패 ({out_path.name})")
    print("=" * 60)
    # 저장 실패를 파이프라인이 성공으로 오판하지 않도록 결과를 반환한다.
    return {"success": bool(saved), "output": str(out_path)}

if __name__ == "__main__":
    result = main()
    sys.exit(0 if (result or {}).get("success") else 1)
