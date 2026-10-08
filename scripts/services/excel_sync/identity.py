# -*- coding: utf-8 -*-
"""엑셀 행 신원 비교와 보류 큐 상수."""
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
DEFAULT_JSON_PATH = DEFAULT_SYSTEM_DIR / "data_requests.json"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 보류 큐 작업의 재시도 한도. 초과하면 `.excel_pending_failed.json`으로 격리한다.
# 한도가 없으면 영구 실패 작업 하나가 큐 선두에서 뒤의 정상 작업을 무한히 막는다.
MAX_PENDING_ATTEMPTS = 5

# 격리 사유. "conflict"는 엑셀의 같은 ID 행이 다른 항목이라 반영을 거부한 작업이다.
# 이런 작업의 값은 DB의 정본이 아니므로 엑셀→DB 동기화 보호 대상에서 뺀다.
QUARANTINE_CONFLICT = "conflict"
QUARANTINE_RETRY_LIMIT = "retry_limit"

# 엑셀 행이 이 항목의 행이 맞는지 판단할 때 앞에서부터 쓰는 필드. 양쪽에 값이 있는
# 첫 필드가 판정을 정한다. (감사 R3-02)
ROW_IDENTITY_FIELDS = ("title", "requester", "aide", "seq_no")


class ExcelApplyConflict(Exception):
    """같은 대장ID의 엑셀 행이 반영하려는 항목과 다른 항목일 때 발생한다."""


def _norm_identity(field: str, value) -> str:
    text = re.sub(r"\s+", "", str(value if value is not None else ""))
    if field == "seq_no" and text.isdigit():
        text = text.lstrip("0") or "0"
    return text


def identity_matches(row_values: Dict[str, Any], reference: Optional[Dict[str, Any]]) -> Optional[bool]:
    """엑셀 행과 기준 레코드가 같은 항목인가.

    양쪽 모두 값이 있는 첫 필드로 판정한다. 비교할 수 있는 필드가 없으면 None(판단 불가).
    """
    if not reference:
        return None
    for field in ROW_IDENTITY_FIELDS:
        a = _norm_identity(field, row_values.get(field))
        b = _norm_identity(field, reference.get(field))
        if a and b:
            return a == b
    return None
