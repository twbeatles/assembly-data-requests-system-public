# -*- coding: utf-8 -*-
"""CLI/MCP 공용 결과 계약 모델.

`datareq-result/v1` 봉투는 고정 필드만 쓴다. `data`/`meta` 세부 구조는 명령별로
달라지며 각 명령 함수가 JSON Schema 호환 dict를 만든다.
"""

RESULT_SCHEMA = "datareq-result/v1"
SEARCH_SCHEMA = "datareq-search/v1"
EVIDENCE_SCHEMA = "datareq-evidence-pack/v1"


def result_envelope(operation, data=None, warnings=None, error=None,
                    source="local_sqlite", dataset_version="",
                    returned=0, elapsed_ms=0, request_id=None):
    """표준 결과 봉투를 만든다. `--json` 출력은 이 dict 하나만 stdout에 낸다."""
    return {
        "schema": RESULT_SCHEMA,
        "ok": error is None,
        "request_id": request_id,
        "operation": operation,
        "data": data if data is not None else {},
        "warnings": list(warnings or []),
        "error": error,
        "meta": {
            "source": source,
            "dataset_version": dataset_version,
            "returned": returned,
            "elapsed_ms": elapsed_ms,
        },
    }


def error_envelope(operation, code, message, dataset_version="",
                   warnings=None, request_id=None):
    """실패 봉투. `error`는 {code, message} 형태다."""
    return result_envelope(
        operation,
        data={},
        warnings=warnings,
        error={"code": code, "message": message},
        dataset_version=dataset_version,
        returned=0,
        elapsed_ms=0,
        request_id=request_id,
    )
