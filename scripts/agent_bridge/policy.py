# -*- coding: utf-8 -*-
"""권한·경로·응답 상한 정책. 코드는 여기서 집행한다 (annotation이 아님).

- `--data-root`는 허용 루트 안에서만 연다. `resolve()` 후 상위폴더/UNC 우회를 검사한다.
- 조회 결과는 기본 마스킹(`internal-safe`)한다. 개인정보 원문은 발췌에도 최소화한다.
"""

from pathlib import Path
from typing import List, Optional

from .errors import DataReqError

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_SOURCES = 10
MAX_EXCERPT_CHARS = 2000
MAX_SNIPPET_CHARS = 300
MAX_RESPONSE_BYTES = 256 * 1024

# 프로파일별 가시 필드. reader가 기본. operator 승인은 P4까지 열지 않는다.
PROFILES = ("reader", "analyst", "operator")

# 대장 상세에서 프로파일별로 가리는 필드. reader/analyst는 연락성 필드를 마스킹한다.
MASKED_LEDGER_FIELDS = ("contact_person", "aide", "note")

READ_ONLY_OPERATIONS = frozenset({
    "doctor", "search", "document_get", "qa_get", "ledger_list",
    "ledger_get", "ledger_summary", "ledger_history", "reconcile",
    "pipeline_plan", "evidence_pack",
})


def clamp_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT):
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def clamp_chars(value, default, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def resolve_allowed_root(target, allowed_roots):
    """target이 허용 루트 안에 있는지 확인하고 실제 경로를 돌려준다.

    허용 루트 밖·상위 경로(`..`)·존재하지 않는 경로는 `policy_denied`로 거절한다.
    """
    candidate = Path(target).expanduser()
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError):
        raise DataReqError("policy_denied", "허용되지 않은 경로입니다.")
    for root in allowed_roots or []:
        try:
            base = Path(root).expanduser().resolve()
        except (OSError, ValueError):
            continue
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            continue
    raise DataReqError("policy_denied", "허용된 데이터 루트 밖의 경로입니다.")


def default_allowed_roots(base_dir):
    return [Path(base_dir)]


def mask_text(text):
    """발췌·스니펫용 최소 마스킹. 기존 `mask_pii`를 재사용한다."""
    if not text:
        return ""
    try:
        from extractors.text_extractor import mask_pii
        return mask_pii(str(text))
    except Exception:
        return str(text)


def mask_record(record, fields=MASKED_LEDGER_FIELDS):
    """민감 필드를 마스킹한 사본을 돌려준다. 원본 dict는 바꾸지 않는다."""
    masked = dict(record)
    for field in fields:
        if masked.get(field):
            masked[field] = mask_text(masked[field])
    return masked


def check_budget(payload_chars):
    """단일 응답 상한을 넘는지 본다. 넘으면 호출자가 낮추거나 오류를 낸다."""
    return payload_chars <= MAX_RESPONSE_BYTES


def response_too_large(text):
    """직렬화된 최종 응답이 UTF-8 바이트 상한을 넘는지 본다.

    limit·발췌 길이 제한과 별개로 대장 details/note·이력·긴 title 합이 상한을 넘을 수
    있어, 최종 직렬화 결과 기준으로 집행한다. (감사 Gap-1)
    """
    try:
        size = len(text.encode("utf-8"))
    except Exception:
        return True
    return size > MAX_RESPONSE_BYTES


def budget_error(operation):
    """상한 초과 시의 명시적 오류. 무통보 잘림 대신 오류를 낸다."""
    from .errors import DataReqError
    return DataReqError(
        "policy_denied",
        "응답이 상한(%d 바이트)을 초과했습니다. "
        "limit·max_chars·max_sources를 낮춰 다시 시도하세요." % MAX_RESPONSE_BYTES)


def describe_limits():
    return {
        "default_limit": DEFAULT_LIMIT,
        "max_limit": MAX_LIMIT,
        "max_sources": MAX_SOURCES,
        "max_excerpt_chars": MAX_EXCERPT_CHARS,
        "max_snippet_chars": MAX_SNIPPET_CHARS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
    }
