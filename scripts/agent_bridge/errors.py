# -*- coding: utf-8 -*-
"""안정적 오류 코드와 CLI 종료 코드.

종료 코드: 0=성공, 2=입력 오류, 3=데이터셋/의존성 없음, 4=충돌/사용 중,
5=정책 거부, 6=부분/검증 실패, 1=예상 밖 오류. 기존 배치 진입점 코드는 바꾸지 않는다.
"""

SUCCESS = 0
UNEXPECTED = 1
INVALID_INPUT = 2
MISSING_DATASET = 3
CONFLICT_BUSY = 4
POLICY_DENIED = 5
PARTIAL_FAILURE = 6

ERROR_CODES = {
    "ok": SUCCESS,
    "invalid_input": INVALID_INPUT,
    "dataset_unavailable": MISSING_DATASET,
    "index_error": MISSING_DATASET,
    "no_results": SUCCESS,
    "conflict": CONFLICT_BUSY,
    "pipeline_busy": CONFLICT_BUSY,
    "policy_denied": POLICY_DENIED,
    "partial": PARTIAL_FAILURE,
    "unexpected": UNEXPECTED,
}


class DataReqError(Exception):
    """Facade/CLI/MCP가 공통으로 쓰는 오류. 코드는 문자열, 종료 코드는 매핑표."""

    def __init__(self, code, message):
        super(DataReqError, self).__init__(message)
        self.code = code
        self.message = message

    @property
    def exit_code(self):
        return ERROR_CODES.get(self.code, UNEXPECTED)
