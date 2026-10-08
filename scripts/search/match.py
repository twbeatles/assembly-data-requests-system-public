# -*- coding: utf-8 -*-
"""파이썬 대체 검색 평가(SRP: 행 매칭·배점)."""
import re

from search.lang import EXACT_MATCH_FIELDS, _CHOSEONG_MAX_LEN, strip_particle, get_choseong
from search.parse import _expand_terms


def _nospace_lower(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()
def _term_matches(term: str, blob_nospace: str, choseong_blob: str, use_synonyms: bool = False) -> bool:
    lower = term.lower()
    if re.fullmatch(r"[ㄱ-ㅎ]+", lower):
        return lower in choseong_blob
    variants = {lower, strip_particle(lower)}
    if use_synonyms:
        variants.update(_expand_terms(lower, True))
    return any(v and _nospace_lower(v) in blob_nospace for v in variants)
def fields_match(plan: dict, row) -> bool:
    """`필드:값` 한정자를 한 행에 적용한다 (제안서 S6).

    화면(대시보드)과 서버가 같은 규칙을 써야 결과가 갈라지지 않는다(불변조건 19와 같은 취지).
    """
    fields = plan.get("fields") or {}
    if not fields:
        return True

    def _get(name):
        try:
            return str(row[name] or "")
        except (KeyError, IndexError, TypeError):
            return ""

    for key, wanted in fields.items():
        if key in ("date_from", "date_to"):
            continue
        actual = _nospace_lower(_get(key))
        if key in EXACT_MATCH_FIELDS:
            # 상태 같은 값은 부분일치로 보면 안 된다. `상태:제출`이 `미제출`에도 걸린다.
            if not any(_nospace_lower(w) == actual for w in wanted):
                return False
        elif not any(_nospace_lower(w) in actual for w in wanted):
            # 이름·기관은 부분일치가 편하다. `요구자:홍길동`가 "홍길동 의원"에 걸려야 한다.
            return False

    date_from = fields.get("date_from")
    date_to = fields.get("date_to")
    if date_from or date_to:
        actual = _get("request_date").strip()
        if not actual:
            return False
        if date_from and actual < date_from:
            return False
        if date_to and actual > date_to:
            return False
    return True
def row_matches(plan: dict, texts, choseong_texts=(), use_synonyms: bool = False) -> bool:
    """검색 계획을 파이썬으로 평가한다. 띄어쓰기 무시·조사 분리·초성 검색을 지원한다.

    texts: 본문 등 전체 비교 대상 문자열들. choseong_texts: 초성 비교에 쓸 짧은 필드(제목·요구자).

    필드를 **하나로 이어 붙이지 않고 하나씩 지연 평가**한다. 검색 대상 순서는 짧은 필드가
    앞이고 문서 전문이 맨 뒤라, 제목에서 찾으면 30 MB짜리 본문은 건드리지도 않는다.
    예전에는 모든 필드를 이어 붙여 공백 제거 사본을 만들었고, 그 사본 하나가 행마다
    수만 자 복사였다.
    """
    texts = list(texts)
    _cache: list[str | None] = [None] * len(texts)

    def _norm(i):
        if _cache[i] is None:
            _cache[i] = _nospace_lower(texts[i])
        return _cache[i]

    def _any_field_has(needle):
        if not needle:
            return False
        for i in range(len(texts)):
            if needle in _norm(i):
                return True
        return False

    # 제외어는 어느 필드에 있어도 탈락이므로 먼저 본다.
    for n in plan["neg"]:
        if _any_field_has(_nospace_lower(n)):
            return False

    if not plan["groups"] and not plan["phrases"]:
        return True

    for p in plan["phrases"]:
        if not _any_field_has(_nospace_lower(p)):
            return False

    if not plan["groups"]:
        return True

    choseong_blob = None

    def _term_hit(term):
        nonlocal choseong_blob
        lower = term.lower()
        if re.fullmatch(r"[ㄱ-ㅎ]+", lower):
            if choseong_blob is None:
                choseong_blob = _nospace_lower(" ".join(get_choseong(c) for c in choseong_texts))
            return lower in choseong_blob
        variants = {lower, strip_particle(lower)}
        if use_synonyms:
            variants.update(_expand_terms(lower, True))
        return any(_any_field_has(_nospace_lower(v)) for v in variants if v)

    return any(all(_term_hit(t) for t in terms) for terms in plan["groups"])
def score_row(plan: dict, weighted_texts, use_synonyms: bool = False) -> int:
    """대체 경로에도 관련도 점수를 준다 (제안서 S7).

    예전에는 파이썬 대체 검색이 모든 행에 rank 1.0을 박아, 2글자·초성·OR 질의에서
    관련도 정렬이 아예 없었다. 대시보드 클라이언트 검색과 같은 배점을 쓴다.

    weighted_texts: [(텍스트, 가중치), ...] — 제목이 무겁고 본문이 가볍다.
    """
    # 초성 변환은 한 글자씩 도는 파이썬 루프라 문서 전문(수만 자)에 걸면 배점 하나에
    # 수 초가 든다. 질의에 초성 토큰이 실제로 있을 때만, 그것도 짧은 텍스트에만 만든다.
    needs_choseong = any(
        re.fullmatch(r"[ㄱ-ㅎ]+", t)
        for terms in (plan.get("groups") or ())
        for t in terms
    )
    score = 0
    for text, weight in weighted_texts:
        blob_nospace = _nospace_lower(text)
        if not blob_nospace:
            continue
        cho = ""
        if needs_choseong and len(blob_nospace) <= _CHOSEONG_MAX_LEN:
            cho = _nospace_lower(get_choseong(text))
        for phrase in plan.get("phrases") or ():
            if _nospace_lower(phrase) in blob_nospace:
                score += weight * 2
        for terms in plan.get("groups") or ():
            for t in terms:
                if _term_matches(t, blob_nospace, cho, use_synonyms):
                    score += weight
    return score
def plan_is_empty(plan: dict) -> bool:
    """검색어가 실질적으로 비어 있는가(필드 한정자만 있는 경우 포함)."""
    return not plan["groups"] and not plan["phrases"] and not plan["neg"]
