# -*- coding: utf-8 -*-
"""기존 `LedgerService` 검색 결과를 `datareq-search/v1` 형태로整える 어댑터.

랭킹·매칭 알고리즘은 건드리지 않는다. 여기서 하는 일은 모양 만들기뿐이다:
ID·발췌·출처 해시·페이지 나눔. FTS 상대 랭킹 점수는 0~100 신뢰도로 포장하지 않는다.
"""

import hashlib
from typing import Any, Dict, List, Optional

from . import policy


def _stable_id(kind, row):
    if kind == "qa":
        return str(row.get("qa_id") or "")
    if kind == "doc":
        return str(row.get("doc_id") or "")
    return str(row.get("ledger_id") or "")


def _title_of(kind, row):
    if kind == "qa":
        return str(row.get("question_title") or row.get("title") or "")
    return str(row.get("title") or "")


def _body_of(kind, row):
    if kind == "qa":
        return str(row.get("answer_markdown") or row.get("answer_full") or "")
    if kind == "doc":
        return str(row.get("full_markdown") or "")
    return str(row.get("details") or row.get("note") or "")


def _doc_ref_of(kind, row):
    if kind == "qa":
        return str(row.get("doc_id") or "") or None
    if kind == "doc":
        return str(row.get("doc_id") or "") or None
    return str(row.get("linked_doc_id") or "") or None


def content_hash(text):
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def locate_snippet(body, query, max_chars=policy.MAX_SNIPPET_CHARS):
    """질의어 적중 위치 앞뒤를 잘라 발췌와 범위를 돌려준다.

    정규화 텍스트라 정확한 위치를 복원하지 못하면 범위를 None으로 둔다.
    """
    text = body or ""
    needle = (query or "").strip().split()
    hit = -1
    token = ""
    low = text.lower()
    for part in needle:
        if len(part) < 2:
            continue
        found = low.find(part.lower())
        if found >= 0:
            hit = found
            token = part
            break
    if hit < 0:
        head = text[:max_chars].strip()
        truncated = len(text) > len(head)
        excerpt = head + ("…" if truncated else "")
        return excerpt, None, truncated
    start = max(0, hit - 60)
    end = min(len(text), hit + len(token) + 60)
    excerpt = (("…" if start > 0 else "") + text[start:end].strip()
               + ("…" if end < len(text) else ""))
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "…"
        return excerpt, None, True
    return excerpt, {"start": start, "end": min(end, start + len(excerpt))}, end < len(text)


def shape_item(kind, row, query, max_chars=policy.MAX_SNIPPET_CHARS):
    stable = _stable_id(kind, row)
    body = _body_of(kind, row)
    excerpt, snippet_range, _ = locate_snippet(body, query, max_chars=max_chars)
    return {
        "kind": kind,
        "source_id": "%s:%s" % (kind, stable),
        "document_id": _doc_ref_of(kind, row),
        "title": _title_of(kind, row),
        "year": str(row.get("year") or ""),
        "snippet": policy.mask_text(excerpt),
        "snippet_range": snippet_range,
        "location_quality": "known" if snippet_range else "unknown",
        "retrieval_method": "fts_or_fallback",
        "score": row.get("rank"),
        "score_note": "relative rank (lower is better for FTS, higher for fallback)",
        "citation": {
            "source_id": "%s:%s" % (kind, stable),
            "hash": content_hash(body),
        },
    }


def paginate(items, limit, cursor):
    """오프셋 커서 나눔. cursor는 10진 오프셋 문자열이다."""
    try:
        offset = max(0, int(cursor)) if cursor not in (None, "") else 0
    except (TypeError, ValueError):
        offset = 0
    page = items[offset:offset + limit]
    rest = offset + len(page)
    return page, (str(rest) if rest < len(items) else None)
