# -*- coding: utf-8 -*-
"""검색 질의 정규화. 웹 API·GUI가 같은 규칙을 쓰도록 한다.

지원 문법(대시보드 클라이언트 검색과 같은 기본 문법, 감사 R3-03):
- 공백: AND
- `OR` 또는 `|`: OR 그룹
- `-단어`: 제외
- `"구문"`: 정확한 구문(모든 OR 그룹에 적용)
- `필드:값`: 필드 한정. 요구자·상태·연도·문서번호·정당·부서·기간 (제안서 S6)
- 초성만 입력(ㄷㅍㅇㅋ), 띄어쓰기 무시, 조사 분리: FTS로 표현할 수 없어 파이썬 필터가 처리한다.

동의어는 `search_synonyms.SYNONYMS`가 정본이고, 서버·GUI·대시보드가 같은 표를 쓴다(제안서 S4).

**trigram 토크나이저는 3글자 미만을 색인하지 않는다.** 2글자 토큰을 MATCH에 넣으면 결과가
항상 0건이라, 예전에는 그 0건을 "FTS가 못 찾음"으로 보고 전 행을 파이썬으로 훑었다.
`국회`·`삭제` 같은 업무 핵심어가 모두 여기 걸려 10배 느렸다. 이제 2글자 토큰이 하나라도
있으면 FTS를 건너뛰고 곧바로 대체 경로로 보낸다(제안서 S2).
"""

import re






























from search.lang import (
    FTS_MIN_TOKEN_LEN, KOREAN_PARTICLES, CHOSEONG_LIST, _CHOSEONG_MAX_LEN,
    _JAMO_RE, _FTS_RESERVED_RE, FIELD_ALIASES, DATE_FIELDS, EXACT_MATCH_FIELDS,
    _FIELD_TOKEN_RE, _DATE_RANGE_RE, _clean_term, strip_particle, get_choseong,
    normalize_date, NOSPACE_CODEPOINTS,
)
from search.parse import _take_field, parse_query, _expand_terms, build_fts_match
from search.match import (
    _nospace_lower, _term_matches, fields_match, row_matches, score_row, plan_is_empty,
)
