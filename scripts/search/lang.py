# -*- coding: utf-8 -*-
"""검색 어휘 자원(SRP: 상수·정규화·초성·날짜)."""
import calendar
import re


# FTS5 trigram은 3글자 단위로 색인한다. 이보다 짧은 토큰은 MATCH로 표현할 수 없다.
FTS_MIN_TOKEN_LEN = 3
# `_nospace_lower`가 떼는 공백과 SQL 선필터 블롭이 떼는 공백을 한 곳에서 맞춘다. (감사 R6-01)
# Python re `\s`(str 기준)가 매칭하는 코드포인트 전체다. `\x1c`~`\x1f`도 포함되므로
# 기억이 아니라 실측으로 확정했다. SQL 쪽은 `CHAR(n)`으로 같은 집합을 뗀다
# (`services/ledger/service.py._norm_expr`). 한쪽만 고치면 선필터가 결과를 깎는다.
NOSPACE_CODEPOINTS = (
    9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
    8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202,
    8232, 8233, 8239, 8287, 12288,
)
KOREAN_PARTICLES = (
    "에서", "으로", "에게", "까지", "부터", "보다", "처럼",
    "을", "를", "이", "가", "은", "는", "의", "에", "로",
    "과", "와", "도", "만", "나", "이나",
)
CHOSEONG_LIST = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
# 초성 사본을 만들 최대 길이. 문서 전문에 초성 변환을 걸면 검색 한 번에 수백만 글자를 돈다.
# 초성 검색은 제목·요구자처럼 짧은 필드에서만 뜻이 있다.
_CHOSEONG_MAX_LEN = 4000
_JAMO_RE = re.compile(r"[ㄱ-ㅎ]")
_FTS_RESERVED_RE = re.compile(r'["\'*^(){}\[\]:~\-\\/]')

# `필드:값` 한정자. **여기 있는 이름만** 한정자로 본다. 모르는 접두사(`테스트:질의`)는
# 평범한 검색어로 두고 `:`를 떼어낸다. 그러지 않으면 콜론이 든 검색어가 조용히 사라진다.
FIELD_ALIASES = {
    "요구자": "requester", "의원": "requester", "requester": "requester",
    "상태": "status", "진행상태": "status", "status": "status",
    "연도": "year", "년도": "year", "year": "year",
    "문서번호": "doc_number", "docno": "doc_number",
    "연번": "seq_no", "seq": "seq_no",
    "정당": "party", "party": "party",
    "부서": "department", "담당부서": "department", "department": "department",
    "기관": "institution", "institution": "institution",
    "기간": "date", "일자": "date", "date": "date",
}
DATE_FIELDS = ("date",)
# 값 전체가 같아야 하는 필드. 부분일치로 두면 `상태:제출`이 `미제출`에도 걸린다.
EXACT_MATCH_FIELDS = ("status", "year", "seq_no", "doc_number")
_FIELD_TOKEN_RE = re.compile(r"^([0-9A-Za-z가-힣]+):(.*)$")
_DATE_RANGE_RE = re.compile(r"^(?P<a>[0-9][0-9\-.]*)?\.\.(?P<b>[0-9][0-9\-.]*)?$")
def _clean_term(term: str) -> str:
    return re.sub(r"[^\w가-힣ㄱ-ㅎ]", "", _FTS_RESERVED_RE.sub(" ", term)).strip()
def strip_particle(word: str) -> str:
    if not word or len(word) <= 2:
        return word
    for p in KOREAN_PARTICLES:
        if len(word) > len(p) + 1 and word.endswith(p):
            return word[: -len(p)]
    return word
def get_choseong(text: str) -> str:
    out = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            out.append(CHOSEONG_LIST[(code - 0xAC00) // (21 * 28)])
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)
def normalize_date(value: str, end_of_range: bool = False) -> str:
    """`2026`, `2026-03`, `2026-03-01`을 비교 가능한 `YYYY-MM-DD`로 편다.

    `request_date`가 항상 `YYYY-MM-DD`라서 문자열 비교만으로 범위 판정이 된다.
    """
    v = re.sub(r"[.]", "-", str(value or "").strip())
    if not v:
        return ""
    parts = [p for p in v.split("-") if p]
    if not parts or not parts[0].isdigit():
        return ""
    year = parts[0].zfill(4)
    if len(parts) == 1:
        return f"{year}-12-31" if end_of_range else f"{year}-01-01"
    month = parts[1].zfill(2)
    if len(parts) == 2:
        if end_of_range:
            try:
                last = f"{calendar.monthrange(int(year), int(month))[1]:02d}"
            except (ValueError, OverflowError):
                # 월·연도가 달력 범위 밖이면 예전 고정 표로 떨어진다. (감사 R6-05)
                last = {"01": "31", "03": "31", "05": "31", "07": "31", "08": "31",
                        "10": "31", "12": "31", "04": "30", "06": "30", "09": "30",
                        "11": "30"}.get(month, "28")
            return f"{year}-{month}-{last}"
        return f"{year}-{month}-01"
    return f"{year}-{month}-{parts[2].zfill(2)}"
