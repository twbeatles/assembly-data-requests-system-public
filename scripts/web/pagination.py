# -*- coding: utf-8 -*-
"""목록 페이지네이션. 전역 상태가 없는 순수 함수(SRP: 페이징)."""

MAX_PAGE_LIMIT = 1000


def parse_page_params(query):
    """`limit`(1~1000)·`offset`(0 이상) 쿼리 값. 잘못된 값은 무시한다."""
    limit = None
    offset = 0
    raw_limit = (query.get('limit') or [None])[0]
    raw_offset = (query.get('offset') or [None])[0]
    try:
        if raw_limit not in (None, ''):
            limit = max(1, min(int(raw_limit), MAX_PAGE_LIMIT))
    except ValueError:
        limit = None
    try:
        if raw_offset not in (None, ''):
            offset = max(0, int(raw_offset))
    except ValueError:
        offset = 0
    return limit, offset


def page_slice(rows, limit, offset):
    end = None if limit is None else offset + limit
    return rows[offset:end]


def paged_response(rows, query):
    """목록 응답. `total`은 필터 후 전체 건수, `count`는 이번에 돌려준 건수."""
    limit, offset = parse_page_params(query)
    page = page_slice(rows, limit, offset)
    body = {"success": True, "count": len(page), "total": len(rows), "data": page}
    if limit is not None or offset:
        body.update({"offset": offset, "limit": limit})
    return body
