# -*- coding: utf-8 -*-
"""
HTML Dashboard Template Renderer Module
Follows Single Responsibility Principle (SRP): Handles template loading,
data serialization/compression, variable substitution, and safe atomic file writing.
"""

import os
import sys
import json
import gzip
import base64
import time
import shutil
import html
import re
from pathlib import Path
from typing import Dict, Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_PATH = SCRIPTS_DIR / "templates" / "dashboard_template.html"
DASHBOARD_SRC_DIR = SCRIPTS_DIR / "templates" / "dashboard"
DASHBOARD_MANIFEST_PATH = DASHBOARD_SRC_DIR / "manifest.json"
_EXTERNAL_ASSET_RE = re.compile(r"<(?:script|link)\b[^>]+\b(?:src|href)\s*=", re.I)
from renderer.assemble import AssembleMixin
from renderer.payload import PayloadMixin


class DashboardRenderer(AssembleMixin, PayloadMixin):  # pyright: ignore[reportGeneralTypeIssues]  # static-only cycle; runtime base is object
    """Renders standalone HTML search dashboard with embedded compressed data."""

    # 전문 보존 정책: 대시보드 HTML은 문서·Q&A 본문을 용량 이유로 잘라내거나 빼지 않는다.
    # 01번 무설치 HTML만으로도 모달에 원문 전체가 보여야 한다. (엑셀 셀 3만 자 한도와 무관)
    ALWAYS_EMBED_FULL_TEXT = True

    def __init__(self, template_path: Optional[Path] = None):
        self.template_path = Path(template_path) if template_path else DEFAULT_TEMPLATE_PATH


    @staticmethod

    @staticmethod

    @staticmethod
    def verify_offline_dashboard_html(html_content: str):
        """Decode the actual embedded payload and confirm an HTML-only handoff works."""
        if "__B64_GZIP_DATA__" in html_content or "/* __MARKED_JS__ */" in html_content:
            return False, "대시보드 템플릿 자리표시자가 남아 있습니다"
        if re.search(r"<(?:script|link)\b[^>]+\b(?:src|href)\s*=", html_content, re.I):
            return False, "외부 스크립트 또는 스타일시트 의존성이 있습니다"
        # 본문이 여러 블록(COMPRESSED_BODY_n)으로 나뉘어 있으면 브라우저와 같은 규칙으로 합친 뒤 본다.
        try:
            payload = DashboardRenderer.decode_dashboard_payload(html_content)
        except ValueError as error:
            return False, str(error)
        expected_keys = ("documents", "qa_items", "request_ledger")
        if not isinstance(payload, dict) or not all(key in payload for key in expected_keys):
            return False, "내장 데이터 구조가 불완전합니다"
        if not all("full_markdown" in doc for doc in payload["documents"]):
            return False, "문서 전문(full_markdown)이 HTML에 모두 포함되지 않았습니다"
        # Q&A 전문은 부모 본문 안의 위치 참조로 실릴 수 있다. 참조를 되살린 뒤 검사한다.
        # 키가 있는지만 보는 것보다 강한 검사다 — 실제로 복원되는지까지 확인한다.
        restored = DashboardRenderer.rehydrate_qa_bodies(payload)
        if not all(item.get("answer_markdown") is not None for item in restored["qa_items"]):
            return False, "Q&A 전문(answer_markdown)이 HTML에서 복원되지 않았습니다"
        return True, ""

    @staticmethod
    def rehydrate_qa_bodies(payload: Dict[str, Any]) -> Dict[str, Any]:
        """`am_ref`(부모 본문 안의 시작·길이)를 실제 문자열로 되살린다.

        대시보드의 `00_boot.js`가 브라우저에서 하는 일과 **같은 규칙**이다. 두 구현이
        갈라지면 화면과 검증이 다른 것을 보게 되므로, 규칙을 바꿀 때는 양쪽을 같이 고친다.
        """
        docs_by_id = {d.get("doc_id"): (d.get("full_markdown") or "") for d in payload.get("documents", [])}
        out_qas = []
        for q in payload.get("qa_items", []):
            item = dict(q)
            ref = item.pop("am_ref", None)
            if ref and item.get("answer_markdown") is None:
                body = docs_by_id.get(item.get("doc_id"), "")
                try:
                    start, length = int(ref[0]), int(ref[1])
                    item["answer_markdown"] = body[start:start + length]
                except (TypeError, ValueError, IndexError):
                    item["answer_markdown"] = ""
            item.setdefault("answer_markdown", "")
            out_qas.append(item)
        return {**payload, "qa_items": out_qas}


    @staticmethod
    def save_atomic(out_path: Path, content: str, max_retries: int = 5, retry_delay: float = 0.4) -> bool:
        """Saves content to file with atomic write and retry mechanism."""
        out_path = Path(out_path)
        tmp_out = out_path.with_suffix(".tmp.html")
        last_err = None
        for _ in range(max_retries):
            try:
                tmp_out.write_text(content, encoding="utf-8")
                try:
                    os.replace(tmp_out, out_path)
                except Exception:
                    shutil.copy2(tmp_out, out_path)
                    tmp_out.unlink(missing_ok=True)
                return True
            except (PermissionError, OSError) as e:
                last_err = e
                time.sleep(retry_delay)
        if last_err:
            print(f"대시보드 저장 실패: {last_err}")
        return False
