# -*- coding: utf-8 -*-
import re
from typing import Dict

LEDGER_TEXT_FIELDS = ("requester", "aide", "title", "details", "note")
# 엑셀 행이 이 항목의 행이 맞는지 판단할 때 쓰는 필드. (감사 R3-02)
ROW_FINGERPRINT_FIELDS = ("title", "requester", "seq_no", "aide")


def _clean(value) -> str:
    """JSON null을 "None" 문자열로 저장하지 않는다."""
    if value is None:
        return ""
    return str(value).strip()


def next_ledger_id(cur, year: str) -> str:
    """REQ-YYYY- 접두사를 가진 ID 중 가장 큰 번호 + 1.

    연도(year 컬럼)로 찾으면, 가장 큰 번호의 항목을 다른 연도로 옮긴 순간 MAX가 작아져
    기존 ID와 충돌하고 그 연도의 등록이 영구히 실패한다. (감사 R3-08)
    """
    prefix = f"REQ-{year}-"
    rows = cur.execute(
        "SELECT ledger_id FROM request_ledger WHERE substr(ledger_id, 1, ?) = ?",
        (len(prefix), prefix),
    ).fetchall()
    max_num = 0
    for row in rows:
        m = re.fullmatch(r"REQ-\d{4}-(\d+)", str(row[0] or ""))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"{prefix}{max_num + 1:03d}"


def row_fingerprint(record) -> Dict[str, str]:
    record = dict(record) if record else {}
    return {f: _clean(record.get(f)) for f in ROW_FINGERPRINT_FIELDS}


def _seq_token_variants(seq_no: str):
    """연번에서 숫자 토큰 집합을 만든다. 선행 0을 제거한 값과 원문을 모두 담는다."""
    digits = re.sub(r'\D', '', str(seq_no or ""))
    if not digits:
        return set()
    variants = {digits}
    stripped = digits.lstrip('0')
    if stripped:
        variants.add(stripped)
    return variants


def _has_seq_token(text: str, variants) -> bool:
    """숫자 토큰이 다른 숫자에 붙어 있지 않은 경우에만 일치로 본다."""
    blob = str(text or "")
    for v in variants:
        if re.search(rf'(?<!\d){re.escape(v)}(?!\d)', blob):
            return True
    return False

