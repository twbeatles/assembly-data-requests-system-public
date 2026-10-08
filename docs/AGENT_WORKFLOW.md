# 에이전트 작업 순서 (검색 → 원문 확인 → 근거 선택 → 초안 → 검증)

> MCP·CLI로 과거 답변을 찾을 때의 권장 순서다. 프롬프트는 권한 없는 원문 접근을
> 우회하지 못하며, 검증되지 않은 초안은 확정 답변으로 표시하지 않는다.

## 1. 검색

- `datareq_search`로 후보를 모은다 (`query`, `category`, `year`, `limit`, `cursor`).
- 목록 인용은 `status: unverified`다. 점수는 상대 랭킹이며 신뢰도가 아니다.
- 결과가 0건이면 `no_results`다. 인덱스 오류(`index_error`)·데이터 없음
  (`dataset_unavailable`)과 구분해서 다음 행동을 정한다.

## 2. 원문 확인

- `datareq_get_document` / `datareq_get_qa`로 발췌를 이어 읽는다
  (`max_chars` 상한 2000, `offset`·`next_offset`·`truncated` 확인).
- 긴 전문을 한 번에 달라고 하지 않는다. 필요한 구간만 여러 번 호출한다.

## 3. 근거 선택

- `datareq_evidence_pack`에 `question`과 선택한 `source_id`를 넘긴다.
- 패키지의 `content_sha256`은 원문 정본 해시다. `verification`이
  `source_text_match`가 아니면 원문을 다시 확인한다.

## 4. 초안

- 모든 문장에 근거 ID 또는 `미검증` 표시를 붙인다.
- 예전 제출 답변에서 최신 수치를 추측하지 않는다. 최신 공식 데이터가 필요하면
  `unresolved`에 "추가 검증 필요"로 남긴다.

## 5. 검증·승인

- 외부 모델 전송은 조직 정책 검토 후 opt-in이다. 발췌에도 개인정보가 있을 수 있다.
- 운영자 승인 없이 관리대장을 바꾸는 호출은 없다 (v1 도구에 쓰기 없음).
- `letter`·`ledger` 변경은 02번 웹·GUI의 기존 승인 경로를 쓴다.
