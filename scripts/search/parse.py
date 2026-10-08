# -*- coding: utf-8 -*-
"""검색 질의 파싱(SRP: plan·FTS MATCH 생성)."""
import re

from search.lang import (
    FTS_MIN_TOKEN_LEN, _JAMO_RE, FIELD_ALIASES, _FIELD_TOKEN_RE,
    _DATE_RANGE_RE, _clean_term, strip_particle, normalize_date,
)


def _take_field(raw: str, plan: dict) -> bool:
    """`필드:값` 토큰이면 plan["fields"]에 담고 True. 아니면 False."""
    m = _FIELD_TOKEN_RE.match(raw)
    if not m:
        return False
    key = FIELD_ALIASES.get(m.group(1).lower())
    if not key:
        return False
    value = m.group(2).strip().strip('"')
    if not value:
        return True
    if key == "date":
        rng = _DATE_RANGE_RE.match(value)
        if rng:
            start = normalize_date(rng.group("a") or "")
            end = normalize_date(rng.group("b") or "", end_of_range=True)
        else:
            start = normalize_date(value)
            end = normalize_date(value, end_of_range=True)
        if start:
            plan["fields"]["date_from"] = start
        if end:
            plan["fields"]["date_to"] = end
        return True
    plan["fields"].setdefault(key, []).append(value.lower())
    return True
def parse_query(kw: str) -> dict:
    """검색어를 {"groups", "phrases", "neg", "fields"}로 나눈다."""
    plan = {"groups": [], "phrases": [], "neg": [], "fields": {}}
    if not kw:
        return plan
    text = str(kw)

    def _take_phrase(m):
        phrase = re.sub(r"\s+", " ", m.group(1)).strip()
        if phrase:
            plan["phrases"].append(phrase)
        return " "

    text = re.sub(r'"([^"]*)"', _take_phrase, text)
    for block in re.split(r"\s+(?:OR|or|\|)\s+|\s*\|\s*", text):
        terms = []
        for raw in block.split():
            if raw.upper() == "OR" or raw == "|":
                continue
            if raw.startswith("-") and len(raw) > 1:
                neg = _clean_term(raw[1:])
                if neg:
                    plan["neg"].append(neg)
                continue
            if _take_field(raw, plan):
                continue
            term = _clean_term(raw)
            if term:
                terms.append(term)
        if terms:
            plan["groups"].append(terms)
    return plan
def _expand_terms(term: str, use_synonyms: bool) -> list:
    """동의어까지 펼친 낱말 목록. 사전이 없거나 꺼져 있으면 자기 자신뿐."""
    if not use_synonyms:
        return [term]
    try:
        from search_synonyms import expand
    except ImportError:
        return [term]
    variants = expand(term)
    stem = strip_particle(term)
    if stem and stem != term:
        for v in expand(stem):
            if v not in variants:
                variants.append(v)
    return variants or [term]
def build_fts_match(kw: str, use_synonyms: bool = False) -> str:
    """FTS5 MATCH 문자열. 기본 AND, OR 그룹, NOT 제외어, 구문 검색을 지원한다.

    FTS5 연산자 우선순위(NOT > AND > OR)를 이용하되, 동의어 묶음은 괄호로 감싼다.
    초성이 들어 있거나 **3글자 미만 토큰이 하나라도 있으면** 빈 문자열을 돌려
    호출자가 파이썬 대체 검색을 쓰게 한다. trigram이 표현할 수 없는 질의이기 때문이다.
    """
    plan = parse_query(kw)
    if _JAMO_RE.search(str(kw or "")):
        return ""
    phrases = [p.replace('"', "") for p in plan["phrases"] if len(p.replace(" ", "")) >= FTS_MIN_TOKEN_LEN]
    # 짧은 구문이나 짧은 제외어도 FTS로는 정확히 못 거른다. 통째로 대체 경로에 맡긴다.
    if any(len(p.replace(" ", "")) < FTS_MIN_TOKEN_LEN for p in plan["phrases"]):
        return ""
    if any(len(n) < FTS_MIN_TOKEN_LEN for n in plan["neg"]):
        return ""

    groups = []
    for terms in plan["groups"] or ([[]] if phrases else []):
        if any(len(t) < FTS_MIN_TOKEN_LEN for t in terms):
            return ""
        parts = []
        for t in terms:
            variants = [v for v in _expand_terms(t, use_synonyms) if len(v) >= FTS_MIN_TOKEN_LEN]
            if not variants:
                return ""
            if len(variants) == 1:
                parts.append(f'"{variants[0]}"')
            else:
                parts.append("(" + " OR ".join(f'"{v}"' for v in variants) + ")")
        parts += [f'"{p}"' for p in phrases]
        if not parts:
            continue
        expr = " AND ".join(parts)
        for n in plan["neg"]:
            expr += f' NOT "{n}"'
        groups.append(expr)
    return " OR ".join(groups)
