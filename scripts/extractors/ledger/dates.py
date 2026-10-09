# -*- coding: utf-8 -*-
import datetime
import sys
import re
from pathlib import Path
from typing import List

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_DIR = SCRIPTS_DIR.parent
DEFAULT_DB_PATH = DEFAULT_SYSTEM_DIR / "data_requests.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

def _valid_date(y: int, m: int, d: int) -> bool:
    try:
        datetime.date(y, m, d)
        return True
    except ValueError:
        return False


# `(수)`, `(월요일)` 같은 요일 꼬리. 날짜 판정에는 쓰지 않고 떼어 낸다.
_WEEKDAY_SUFFIX = r'(?:\(\s*[월화수목금토일](?:요일)?\s*\))?'
_ISO_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


def coerce_ledger_date(val, year_str: str = ""):
    """웹 입력 날짜를 `YYYY-MM-DD`로 맞춘다. 빈 값은 "", 달력에 없거나 읽을 수 없는 값은 None.

    엑셀 파서(`normalize_ledger_date`)와 **같은 표기**를 받아들인다. 웹이 더 좁게 받으면,
    엑셀에서 들어와 화면에 그대로 보이는 값을 사용자가 다시 저장할 수 없게 된다.
    """
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    out = normalize_ledger_date(s, year_str)
    m = _ISO_DATE_RE.match(out)
    if not m or not _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3))):
        return None
    return out


def normalize_ledger_date(val, year_str: str) -> str:
    """Normalizes various date formats in the request ledger to YYYY-MM-DD.

    달력에 없는 날짜(`0231`, `251345`)는 만들어 내지 않고 원래 값을 돌려준다. 예전에는 6·8자리
    숫자를 검증 없이 조립해 `2025-13-45` 같은 값이 마감일로 들어가 마감 판정에서 조용히 빠졌다.
    6자리(YYMMDD)는 2010년~(올해+1)년만 받는다. `991231`을 2099년으로 읽지 않기 위해서다.
    (감사 R4 4.3 — 2026-09-17 운영 대장 점검 결과 해당 값은 없었다)
    """
    if not val:
        return ""
    s = str(val).strip()
    m_dt = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m_dt:
        return f"{m_dt.group(1)}-{m_dt.group(2)}-{m_dt.group(3)}"
    # 공문서 표기 `2026.9.10`, `2026. 9. 10.`, `2026년 9월 10일`, `2026/9/10(수)`.
    # 예전에는 이 표기를 원문 그대로 남겨, 웹 수정의 날짜 검증이 사용자가 건드리지 않은
    # 이 값 때문에 수정 전체를 거절했다. (감사 7회차 ISSUE-003)
    m_ymd = re.match(r'^(\d{4})\s*[\.\-\/년]\s*(\d{1,2})\s*[\.\-\/월]\s*(\d{1,2})\s*[\.일]?\s*' + _WEEKDAY_SUFFIX + r'$', s)
    if m_ymd:
        y, m, d = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        if _valid_date(y, m, d):
            return f"{y:04d}-{m:02d}-{d:02d}"
        return s
    m_yymd = re.match(r'^(\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*' + _WEEKDAY_SUFFIX + r'$', s)
    if m_yymd:
        y, m, d = 2000 + int(m_yymd.group(1)), int(m_yymd.group(2)), int(m_yymd.group(3))
        if 2010 <= y <= datetime.date.today().year + 1 and _valid_date(y, m, d):
            return f"{y:04d}-{m:02d}-{d:02d}"
        return s
    try:
        sheet_year = int(str(year_str).strip())
    except (TypeError, ValueError):
        sheet_year = None
    m_md = re.match(r'^(\d{1,2})\s*[\.\/]\s*(\d{1,2})\s*\.?\s*' + _WEEKDAY_SUFFIX + r'$', s)
    if m_md:
        m, d = int(m_md.group(1)), int(m_md.group(2))
        if sheet_year and _valid_date(sheet_year, m, d):
            return f"{year_str}-{m:02d}-{d:02d}"
    digits = re.sub(r'\D', '', s)
    if len(digits) == 4:
        m, d = int(digits[:2]), int(digits[2:])
        if sheet_year and _valid_date(sheet_year, m, d):
            return f"{year_str}-{m:02d}-{d:02d}"
    elif len(digits) == 6:
        y, m, d = 2000 + int(digits[:2]), int(digits[2:4]), int(digits[4:])
        if 2010 <= y <= datetime.date.today().year + 1 and _valid_date(y, m, d):
            return f"{y:04d}-{m:02d}-{d:02d}"
    elif len(digits) == 8:
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:])
        if _valid_date(y, m, d):
            return f"{y:04d}-{m:02d}-{d:02d}"
    return s

LEDGER_ID_RE = re.compile(r'^REQ-\d{4}-\d+$')

# 대장 엑셀 헤더 별칭의 단일 정본. 파서(load.py)·ID 스탬핑(ids.py)·웹→엑셀 반영기
# (services/excel_sync/apply.py)가 모두 이 표를 쓴다. 예전에는 반영기가 별칭을 따로 들고 있어
# `요구기관`·`질의제목`·`상임위` 헤더 대장에서 웹 수정이 엑셀에 기록되지 않았고, 다음
# 엑셀→DB 동기화가 DB 값을 엑셀 옛값으로 되돌렸다. (감사 R4-04)
# 앞쪽 이름이 우선한다. 한 시트에 두 별칭이 함께 있으면 먼저 나온 이름의 열을 쓴다.
LEDGER_COLUMN_ALIASES = (
    ("seq_no", ("연번", "순번", "No", "No.")),
    ("party", ("소속", "정당", "위원회", "소속정당", "소속위원회", "상임위")),
    ("requester", ("의원", "요구자", "의원명", "요구의원", "의원/기관", "요구기관")),
    ("aide", ("보좌관", "보좌진", "비서관")),
    ("title", ("요구자료", "요구자료명", "제목", "건명", "자료명", "질의제목")),
    ("details", ("세부내역", "세부요구내역", "세부내용", "내용", "상세내역")),
    ("request_date", ("요구일", "접수일자", "요구일자", "접수일", "일자")),
    ("deadline", ("마감일", "마감일자", "제출기한", "기한")),
    ("submit_date", ("제출일", "제출일자", "완료일", "회신일")),
    ("department", ("부서", "담당부서", "소관부서", "처리부서")),
    ("status", ("제출", "제출여부", "진행상태", "상태")),
    ("note", ("비고", "비고사항", "메모")),
    ("request_type", ("요청형태", "요청구분", "형태", "구분")),
    ("ledger_id", ("대장ID", "ledger_id", "관리번호")),
)
LEDGER_COLUMN_ALIAS_MAP = dict(LEDGER_COLUMN_ALIASES)

ID_HEADER_NAMES = LEDGER_COLUMN_ALIAS_MAP["ledger_id"]
SEQ_HEADER_NAMES = LEDGER_COLUMN_ALIAS_MAP["seq_no"]
TITLE_HEADER_NAMES = LEDGER_COLUMN_ALIAS_MAP["title"]
DETAIL_HEADER_NAMES = LEDGER_COLUMN_ALIAS_MAP["details"]
REQUESTER_HEADER_NAMES = LEDGER_COLUMN_ALIAS_MAP["requester"]
LEDGER_VALUE_FIELDS = (
    "year", "seq_no", "party", "requester", "aide", "title", "details", "request_date",
    "deadline", "submit_date", "department", "status", "note", "request_type",
)


def year_sheet_names(sheet_names) -> list:
    """연도 이름 시트를 최신순으로 돌려준다. 연도 시트가 없으면 전체 시트를 쓴다."""
    named = [n for n in sheet_names if re.fullmatch(r"20\d{2}", str(n).strip())]
    named.sort(reverse=True)
    return named or list(sheet_names)


def build_header_map(ws) -> dict:
    """1행을 헤더로 보고 {헤더명: 열번호} 맵을 만든다."""
    header_map = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(1, c).value
        if val:
            header_map[str(val).strip()] = c
    return header_map


def find_id_column(header_map: dict):
    for name in ID_HEADER_NAMES:
        if name in header_map:
            return header_map[name]
    return None


def cell_text(ws, row: int, header_map: dict, names) -> str:
    for name in names:
        c = header_map.get(name)
        if c:
            v = ws.cell(row, c).value
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def is_ledger_data_row(ws, row: int, header_map: dict) -> bool:
    """대장 본문 행 판정. 파서와 ID 스탬핑이 같은 기준을 공유해야 한다."""
    return any([
        cell_text(ws, row, header_map, SEQ_HEADER_NAMES),
        cell_text(ws, row, header_map, TITLE_HEADER_NAMES),
        cell_text(ws, row, header_map, DETAIL_HEADER_NAMES),
        cell_text(ws, row, header_map, REQUESTER_HEADER_NAMES),
    ])

