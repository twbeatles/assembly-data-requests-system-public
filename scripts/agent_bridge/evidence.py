# -*- coding: utf-8 -*-
"""Evidence Pack v1 생성·검증. 파일/메모리 파생물로만 관리하며 DB 스키마는 건드리지 않는다.

- `content_sha256`은 인용한 원문 정본 텍스트의 해시이며 가공 발췌 해시와 구분한다.
- `verification=source_text_match`는 문구 일치만 보증한다. 주장 사실성·법적 타당성·
  현행 통계 여부는 보증하지 않는다.
- 검증되지 않은 초안은 확정 답변으로 표시하지 않는다 (`claims[].state`).
"""

import datetime
import uuid

from . import policy
from . import search as search_adapter
from .errors import DataReqError


def _utcnow_iso():
    return (datetime.datetime.now(datetime.timezone.utc)
            .astimezone().isoformat(timespec="seconds"))


def _fetch_source(facade, source_id):
    """`kind:stable-id`를 단건 조회로 풀어 정본 텍스트와 출처를 돌려준다."""
    if not source_id or ":" not in source_id:
        raise DataReqError("invalid_input", "잘못된 source_id입니다: %s" % (source_id,))
    kind, stable = source_id.split(":", 1)
    if kind == "doc":
        detail = facade.get_document(stable, max_chars=policy.MAX_EXCERPT_CHARS)
        body_key = "full_markdown"
        title = detail.get("title", "")
        year = detail.get("year", "")
        doc_id = stable
        qa_id = None
    elif kind == "qa":
        detail = facade.get_qa(stable, max_chars=policy.MAX_EXCERPT_CHARS)
        title = detail.get("question_title", "")
        year = detail.get("year", "")
        doc_id = detail.get("doc_id")
        qa_id = stable
    elif kind == "ledger":
        row = facade.ledger_get(stable)
        text = str(row.get("details") or row.get("title") or "")
        return {
            "source_id": source_id, "doc_id": row.get("linked_doc_id") or None,
            "qa_id": None, "title": str(row.get("title") or ""),
            "document_year": str(row.get("year") or ""),
            "excerpt": policy.mask_text(text[:policy.MAX_EXCERPT_CHARS]),
            "locator": {"field": "details", "start": 0,
                        "end": min(len(text), policy.MAX_EXCERPT_CHARS)},
            "content_sha256": search_adapter.content_hash(text),
            "verification": "source_text_match",
        }
    else:
        raise DataReqError("invalid_input", "알 수 없는 source 종류입니다: %s" % (kind,))
    # 단건 조회는 마스킹된 발췌만 주므로, 해시 검증용 정본은 내부 연결로 다시 읽는다.
    full_text = _read_full_text(facade, kind, stable)
    excerpt = detail.get("excerpt", "")
    locator = {"field": "full_markdown" if kind == "doc" else "answer_markdown",
               "start": detail.get("offset", 0),
               "end": detail.get("offset", 0) + len(excerpt)}
    verification = ("source_text_match"
                    if full_text and excerpt and excerpt.replace("…", "") in full_text
                    else "unverified")
    return {
        "source_id": source_id,
        "doc_id": doc_id,
        "qa_id": qa_id,
        "title": title,
        "document_year": year,
        "excerpt": excerpt,
        "locator": locator,
        "content_sha256": search_adapter.content_hash(full_text),
        "verification": verification,
    }


def _read_full_text(facade, kind, stable):
    table, key, column = {
        "doc": ("documents", "doc_id", "full_markdown"),
        "qa": ("qa_items", "qa_id", "answer_markdown"),
    }[kind]
    row = facade._single_row(table, key, stable, column)
    return str((row or {}).get(column) or "")


def build_pack(facade, question, selected_ids=None, max_sources=None):
    """근거 패키지를 만든다. 저장은 호출자가 담당하며 여기서는 dict만 반환한다."""
    if not (question or "").strip():
        raise DataReqError("invalid_input", "question은 비어 있을 수 없습니다.")
    try:
        count = int(max_sources) if max_sources is not None else policy.MAX_SOURCES
    except (TypeError, ValueError):
        raise DataReqError("invalid_input", "max_sources는 정수여야 합니다.")
    count = max(1, min(count, policy.MAX_SOURCES))
    ids = list(selected_ids or [])[:count]
    if not ids:
        # ID가 없으면 검색으로 후보를 모으되, 자동 선택은 검증 대상으로만 둔다.
        found = facade.search(question, category="all", limit=count)
        ids = [item["source_id"] for item in found.get("items", [])]
    sources = []
    for source_id in ids[:count]:
        try:
            sources.append(_fetch_source(facade, source_id))
        except DataReqError:
            continue
    unresolved = []
    if not sources:
        unresolved.append("인용 가능한 근거를 찾지 못했습니다. 추가 검증이 필요합니다.")
    if any(s.get("verification") != "source_text_match" for s in sources):
        unresolved.append("미검증 발췌가 포함되어 있습니다. 원문 확인이 필요합니다.")
    return {
        "schema": "datareq-evidence-pack/v1",
        "pack_id": str(uuid.uuid4()),
        "created_at": _utcnow_iso(),
        "query": question,
        "source_dataset": {
            "db_fingerprint": facade.dataset_version(),
            "snapshot_at": _utcnow_iso(),
        },
        "claims": [{"claim_id": "c1", "text": question, "state": "needs_review"}],
        "sources": sources,
        "unresolved": unresolved,
        "redactions": {"applied": True, "profile": "internal-safe"},
    }


def verify_pack(facade, pack):
    """패키지 무결성을 재검증한다. 해시·위치 불일치는 stale/실패로 표시한다."""
    results = []
    ok = True
    for source in (pack or {}).get("sources", []):
        source_id = source.get("source_id", "")
        expected = source.get("content_sha256", "")
        try:
            kind, stable = source_id.split(":", 1)
            if kind in ("doc", "qa"):
                current = _read_full_text(facade, kind, stable)
            elif kind == "ledger":
                row = facade.ledger_get(stable)
                current = str(row.get("details") or row.get("title") or "")
            else:
                raise DataReqError("invalid_input", "unknown kind")
        except DataReqError:
            results.append({"source_id": source_id, "state": "source_missing"})
            ok = False
            continue
        actual = search_adapter.content_hash(current)
        if actual != expected:
            results.append({"source_id": source_id, "state": "source_stale"})
            ok = False
            continue
        locator = source.get("locator") or {}
        field = locator.get("field", "")
        start = locator.get("start", 0)
        end = locator.get("end", 0)
        excerpt = source.get("excerpt", "")
        try:
            valid_range = 0 <= int(start) <= int(end) <= len(current)
        except (TypeError, ValueError):
            valid_range = False
        if excerpt and excerpt.replace("…", "") not in current:
            results.append({"source_id": source_id, "state": "verification_failed"})
            ok = False
        elif not valid_range:
            results.append({"source_id": source_id, "state": "verification_failed"})
            ok = False
        else:
            results.append({"source_id": source_id, "state": "source_text_match",
                            "field": field})
    return {"ok": ok, "results": results}
