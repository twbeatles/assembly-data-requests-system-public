# -*- coding: utf-8 -*-
"""엑셀 셀 본문 정제(SRP: 정본→셀 텍스트)."""
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


def _excel_body(record, canonical_key, legacy_key):
    """엑셀 셀에 넣을 본문. 정본에서 그때그때 3만 자로 줄인다 (제안서 D6).

    예전 DB에는 줄인 사본이 컬럼(`answer_full_text`·`answer_full`)으로 남아 있다.
    정본이 비어 있는 옛 데이터만 그 컬럼을 쓴다.
    """
    from extractors.text_extractor import clean_for_excel
    body = record.get(canonical_key) or ""
    if body:
        return clean_for_excel(body)
    return record.get(legacy_key, "") or ""
def sanitize_text(val):
    if isinstance(val, str):
        return ILLEGAL_CHARACTERS_RE.sub('', val)
    return val
