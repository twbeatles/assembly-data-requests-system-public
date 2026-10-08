# 개발·에이전트 규칙

## 2026-09-27 감사 개선 불변조건

- 대장 등록·수정·삭제·복원과 `ledger_outbox` 기록은 같은 SQLite 트랜잭션이다. 큐 JSON 저장 성공 전에 outbox를 제거하지 않는다. 재빌드에는 outbox와 `ledger_receipts`도 이관한다.
- 편집 기준 `EDIT_LEDGER_VERSION`은 모달을 연 시점에 고정한다. 자동 갱신된 `LEDGER_MAP`에서 다시 가져오지 않는다. 성공한 빈 서버 목록은 `applyServerLedger`로 적용한다.
- 미래 DB 버전은 재빌드·원문 투입·이전·일반 스키마 준비에서 쓰기 전에 거절한다. 재빌드 직전 백업은 WAL의 커밋 내용도 포함해야 한다.
- 살아 있는 파이프라인 PID는 시간으로 회수하지 않는다. `.pipeline.lock.guard`는 OS 잠금의 고정 파일이므로 삭제하지 않는다. 생성 중인 빈 락은 잠김이며 release는 취득한 파일의 소유권을 확인한다.
- 등록 재시도 키는 응답 유실 후에도 유지한다. 같은 키·다른 입력은 충돌이다. 이전 설치본 재이전은 기존 `__migrated_N` 사본의 해시도 확인한다.
- 회귀: `tests/test_audit_round8.py`.

이 프로젝트의 웹 대시보드는 **문서 전문이 보여야 한다.** 용량·슬림화·오프라인 최적화를 이유로 본문을 빼지 않는다.

구현은 기능 패키지로 나뉘어 있다. **구 경로 파일은 shim**이며 import 경로를 바꾸지 않는다.

| 기능 | 구현 | 호환 진입점 |
|---|---|---|
| 엑셀 양방향 동기 | `scripts/services/excel_sync/` | `excel_sync_service.py` |
| 대장 CRUD·검색 | `scripts/services/ledger/` | `ledger_service.py` |
| 대장 엑셀 파서 | `scripts/extractors/ledger/` | `ledger_parser.py` |
| HTTP 보안·다운로드 | `scripts/web/` | `web_server.py` (전역 상태 소유) |
| GUI 검색 헬퍼 | `scripts/gui/search_text.py` | `launcher_gui.py` |
| 파싱 대상 탐색 | `scripts/pipeline/parse/discovery.py` | `parse_all.py` |
| 대장 엑셀 탐색 | `scripts/extractors/ledger/discovery.py` | `ExcelSyncService.find_master_excel` |
| 웹 서버 단일 실행 | `scripts/web/instance_lock.py` | `web_server.run_server` |
| 대시보드 소스 | `scripts/templates/dashboard/` | 조립 캐시 `dashboard_template.html` |
| 웹 페이징·바디·정적·대장위임·GET라우팅 | `scripts/web/pagination.py·body.py·static.py·ledger_api.py·routes_get.py` | `web_server.py` (전역·POST/PUT/DELETE·run_server 유지) |
| GUI 뷰·액션 | `scripts/gui/views.py·actions.py` | `launcher_gui.py` (검색·갱신 로직 유지) |
| 파싱 안전복사·엑셀폴백·이미지 | `scripts/pipeline/parse/safe_copy.py·xlsx_fallback.py·images.py` | `parse_all.py` (단일 파싱·main 유지) |
| 엑셀 리포트 | `scripts/excel_report/` (text·styles·links·sheets) | `generate_excel_db.py` (main·원자 저장 유지) |
| DB 구축 ID·병합 | `scripts/build_db/` (docids·merge) | `extract_and_build_db.py` (스왑·원자 JSON 유지) |
| 범용 패키지 문구·클린DB | `scripts/universal_pkg/` (content·database) | `build_universal_package.py` (조립 유지) |
| 배포 재시도 복사 | `scripts/distribution/copy.py` | `package_distribution.py` (리졸버·문구·main 유지) |
| 배치파일 본문·검증 | `scripts/bats/` (content·validate) | `write_all_bats.py` (기록·main 유지) |
| 원문 투입 | `scripts/ingest/files.py` | `add_documents_smart.py` (상수·실행 유지) |
| 검색 파싱·매칭 | `scripts/search/` (lang·parse·match) | `search_query.py` |
| 대시보드 조립·페이로드 | `scripts/renderer/` (assemble·payload) | `template_renderer.py` (검증·원자 저장 유지) |
| 파싱 마크다운 배치 규칙·정리 | `scripts/pipeline/parse/layout.py` (+ `year_detect.py`) | `parse_all.parse_output_paths`, `reorganize_markdown.py` |
| JSON 캐시 스트리밍 기록 | `scripts/db/json_export.py` | `LedgerService.sync_json_file`, `extract_and_build_db.write_json_atomic` |

패키지를 다시 분할(`tools/split_python_packages.py`)한 뒤에는 `python tools/prune_unused_imports.py --apply`로 복사된 import 머리말을 정리한다. 미사용 import 더미가 미정의 이름 같은 진짜 결함을 가린다(R4-14). 대시보드 JS를 고친 뒤에는 `DashboardRenderer().write_assembled_template()`로 조립 캐시를 다시 만든다(테스트가 캐시를 읽는다).

대시보드 **전달물**은 항상 HTML 한 파일이다. `DashboardRenderer.load_template()`이 CSS·JS를 인라인으로 합친다. `<script src>` / `<link href>`를 넣지 않는다.

## 전문 보존 정책 (후퇴 금지)

1. `01_웹대시보드_실행.bat`으로 HTML만 열어도 상세 모달에 **원문 전체**가 나와야 한다. 웹 관리 서버(02)가 필수가 아니다.
2. 대시보드 페이로드에서 `full_markdown`(문서)과 `answer_markdown`(Q&A)을 삭제·절단하지 않는다.
   - 위치: `scripts/template_renderer.py` `DashboardRenderer.build_slim_payload`
   - 중복인 `answer_full_text`(엑셀 3만 자 잘림본)만 빼도 된다.
3. 모달·인쇄·좌우 비교·전문 복사는 `full_markdown`을 쓴다. `answer_summary`(300자)나 `answer_full_text`(3만 자)를 전문처럼 넣지 않는다.
   - 위치: `scripts/templates/dashboard/js/12_modal.js` `docDisplayBody`, `ensureDocBody` (구: `dashboard_template.html`)
4. `clean_for_excel()`의 3만 자 한도는 **엑셀 시트 전용**이다. 웹 화면·SQLite `full_markdown`에는 적용하지 않는다.
5. 목록 카드의 미리보기(짧은 스니펫)는 목록용이다. 문서를 열면 전문이어야 한다.

회귀 테스트: `tests/test_audit_scope2.py`의 `test_slim_payload_never_drops_full_markdown`, `tests/test_audit_fixes.py`의 대시보드 페이로드 `full_markdown` 존재 검사.

## 관리대장 동기화 불변 조건 (후퇴 금지)

웹 화면·마스터 엑셀·SQLite가 같은 대장을 공유한다. 아래는 감사에서 **실제로 데이터가 사라진 경로**이므로 순서와 규칙을 바꾸지 않는다. 1~12는 2회차 감사(ISSUE-001~008, git `730e560`의 `PROJECT_AUDIT.md`), 13~18은 3회차 감사에서 나왔다(기록: `git show fb33ed9:PROJECT_AUDIT.md`. 4~5회차 기록은 `git show e4a80aa:PROJECT_AUDIT.md`, 6회차 R6-xx와 2026-09-22 one-shot 감사(ISSUE-001~004)는 `git show 5fd0577:PROJECT_AUDIT.md`, 현행 `PROJECT_AUDIT.md`는 7회차(2026-09-23)다. **코드 주석의 `(ISSUE-00x)`는 회차마다 번호가 다시 시작한다** — `(감사 7회차 ISSUE-00x)`처럼 회차를 함께 적는다).

1. **보류 큐를 먼저 반영한다.** 서버 시작과 watcher 주기에서 Excel→DB 동기화보다 `flush_pending_queue()`가 앞서거나, 최소한 큐에 남은 `ledger_id`를 엑셀 기준 덮어쓰기·삭제 판정에서 제외해야 한다. 순서를 뒤집으면 웹 등록이 `[삭제]`가 되고 웹 수정이 엑셀 구값으로 되돌아간다.
   - 위치: `scripts/services/excel_sync/` (`watcher.py`, `excel_to_db.py`, `pending.py`). 구: `excel_sync_service.py` shim
2. **엑셀 행은 `대장ID`로, 워크북 전체 시트에서 찾는다.** 연번·제목 같은 보조 키는 ID 칸이 빈 행에만 쓴다. `insert`는 ID로 못 찾으면 항상 새 행이다. 보조 키를 먼저 쓰면 다른 항목의 행을 덮어쓴다.
   - 대상 연도 시트만 뒤지면 항목의 연도가 바뀌었을 때 원래 행을 못 찾아 **중복 ID 행**이 생기고, 다음 스탬핑이 원래 행에 새 ID를 발급해 **유령 항목**을 만든다. 다른 시트에서 찾으면 전체 필드로 옮기고 원래 행을 지운다.
   - 위치: `_apply_to_excel`의 행 매칭, `_find_row_by_ledger_id`
3. **ID는 행 위치로 매기지 않는다.** `대장ID`가 비어 있으면 `stamp_ledger_ids()`로 값을 기록해 고정한다. 위치 기반 ID는 행 삭제·삽입·정렬만으로 서로 다른 항목의 ID가 뒤바뀐다.
   - 위치: `scripts/extractors/ledger/ids.py` `plan_ledger_ids`, `stamp_ledger_ids` (구: `ledger_parser.py` shim)
4. **기존 행 수정은 바뀐 필드만 엑셀에 쓴다. 단, 새 행을 만들 때는 전체 필드를 쓴다.** "바뀐 필드"는 요청에 들어 있는 필드가 아니라 **기존 값과 다른 필드**다(불변조건 45). 전체 스냅숏으로 기존 행을 덮어쓰면 보류 큐 재생 때 사용자의 수기 수정이 사라진다. 반대로 새 행에 일부 필드만 쓰면 의원·제목 칸이 빈 행이 되고, 다음 Excel→DB 동기화가 그 빈 값으로 **DB의 요구자명을 지운다.** (`item["_changed_fields"]`, `is_new_row`)
5. **연도 시트가 없으면 만든다.** 다른 연도 시트에 행을 넣으면 해당 항목의 연도가 그 시트 연도로 바뀐다.
6. **파이프라인 락 중에는 엑셀 동기화를 하지 않는다.** DB 재구축 중 반영한 변경은 shadow DB 교체 때 사라진다. (`PipelineGuard` 확인)
7. **파이프라인 단계 실패를 성공으로 보고하지 않는다. 단, 문서 일부 실패는 단계 실패가 아니다.** 각 단계는 `{"success": bool}`을 반환하고, CLI는 non-zero로 종료하며 GUI는 오류를 표시한다. 문서 몇 건이 변환에 실패한 것은 `partial_failures`로 따로 보고하고 **나머지 산출물 생성은 계속한다.** 손상 파일 1건 때문에 DB·엑셀·대시보드가 통째로 갱신되지 않으면 안 된다.
   - 위치: `scripts/parse_all.py` `main`, `scripts/add_documents_smart.py` 단계 루프

8. **마스터 엑셀 쓰기는 하나의 락으로 직렬화한다.** `_apply_to_excel`, `stamp_ledger_ids`, `flush_pending_queue`가 모두 `self.lock` 아래에서만 워크북을 연다. openpyxl은 파일 전체를 읽어 전체를 다시 쓰므로, 두 스레드가 동시에 들어가면 나중에 저장한 쪽이 먼저 저장한 쪽의 등록을 통째로 지운다. `_pending_lock`은 큐 자료구조 전용, `_failed_lock`은 격리 파일 전용이다.
   - **락을 함께 쥘 때는 항상 `self.lock` → `_failed_lock` → `_pending_lock` 순서다. 역순 금지.** (예전 문구 "두 락을 동시에 쥐지 않는다"는 사실과 달랐다. `sync_item_to_excel`은 `self.lock` 안에서 큐에 적재하고, `sync_excel_to_db`는 `self.lock` 안에서 보호 필드를 조회한다. 4회차 R4-10) `flush_pending_queue`처럼 `_pending_lock`을 놓은 뒤 `self.lock`을 잡는 것은 괜찮다. 순서는 `tests/test_audit_phase13_r4.py`의 `TestLockOrder`가 검사한다.
   - 이 락은 **프로세스 안에서만** 유효하다. 프로세스 간 직렬화는 불변조건 27(웹 서버 단일 인스턴스)과 6(파이프라인 락)이 맡는다.
   - 위치: `scripts/services/excel_sync/` (`apply.py`, `pending.py`, `service.py`). 구: `excel_sync_service.py` shim

9. **엑셀을 덮어쓰기 전에 백업하고 원자적으로 교체한다.** `save_workbook_atomic()`만 쓴다. `wb.save(원본)`을 직접 부르지 않는다. 저장 중 중단되면 사용자의 마스터 대장이 깨지고 되돌릴 방법이 없다.
   - 위치: `scripts/extractors/ledger/workbook.py` `save_workbook_atomic`, `backup_master_excel` (구: `ledger_parser.py` shim)

10. **삭제 판정은 "엑셀을 읽기 전에 DB에 있던 행"만 대상으로 한다.** 파싱 도중 웹에서 새로 등록된 행은 "엑셀에 없다"가 아니라 "아직 쓰이지 않았다"이므로 판정할 근거가 없다. 이 방어선이 없으면 동기화 중에 누른 등록이 성공 메시지와 함께 곧바로 `[삭제]`가 된다.
    - 위치: `sync_excel_to_db`의 `known_ledger_ids`

11. **`[삭제]` tombstone은 SQLite에만 남기고 파생 산출물에는 넣지 않는다.** DB에 남겨야 stale 엑셀 행이 항목을 되살리지 못한다. 그러나 JSON 캐시·대시보드 HTML·통합 XLSX에 넣으면 삭제한 항목이 오프라인 화면에 다시 보여서, 02번 서버 화면과 01번 오프라인 화면의 대장이 서로 달라진다.
    - 위치: `extract_and_build_db.main`의 `active_ledger_records`, `DashboardRenderer.build_slim_payload`

12. **워크스페이스 루트 판정과 배포 환경 판정은 `system_config` 한 곳에서만 한다.** 연도를 문자열로 박지 않는다. 예전에 `parse_all.py`가 `"2026"`을 하드코딩해, 그 폴더가 없는 배치에서 탐색 루트가 무너지고 파싱 마크다운 전량 삭제 + 빈 DB 스왑으로 이어졌다. 문서 수가 급감하면 어떤 체크아웃에서든 스왑을 막는다(`--allow-shrink`로만 우회).
    - 배포본 판정은 `is_distribution_dir()`(기본 배포 폴더명 또는 `.distribution_manifest.json`)만 쓴다. **경로에 "배포"라는 글자가 있는지로 판단하지 않는다.** 그렇게 해서 범용 배포본의 대장 엑셀이 통째로 무시됐다(3회차 R3-04). 배포본은 자기 폴더가 루트다(R3-14).
    - 위치: `scripts/system_config.py`, `scripts/parse_all.py` `WORKSPACE_ROOT`, `prune_orphan_markdowns`, `scripts/add_documents_smart.py` `IS_DIST_ENV`, `extract_and_build_db.main`의 급감 가드

아래 13~18은 3회차 감사에서 확인한 경로다(기록 위치는 위 참조).

13. **보류 큐 재생은 같은 `대장ID`의 엑셀 행이 그 항목의 행인지 확인한 뒤에만 쓴다.** `insert`인데 ID 행이 이미 있으면, 같은 항목일 때만 허용한다(멱등 재시도). `update`/`delete`는 수정 전 지문(`_expected`)이나 수정 후 값과 맞아야 한다. 맞지 않으면 덮어쓰기·이동·삭제 없이 `conflict`로 격리한다. 오염된 큐 22건이 실제 대장의 1·2번 행을 덮고 2025 시트 행을 지운 것을 복사본에서 재현했다(R3-02).
    - 위치: `scripts/services/excel_sync/apply.py` `_verify_row_identity`, `identity.py` `identity_matches`, `pending.py` `flush_pending_queue`(재생 직전 `backup_master_excel(force=True)`). 구: `excel_sync_service.py` shim

14. **엑셀 반영에 실패한 웹 변경은 이유와 무관하게 보호한다.** `PermissionError`만 큐에 넣으면 저장 검증 실패·디스크 오류일 때 다음 엑셀→DB 동기화가 웹 수정을 옛 값으로 되돌린다. 재시도 한도 초과(`retry_limit`) 격리 작업도 계속 보호한다. 보호는 **그 작업이 바꾼 필드(`_changed_fields`)만** 한다. 행 전체를 보호하면 사용자가 엑셀에서 다른 칸을 고친 내용이 무시된다(R3-07).
    - 위치: `sync_item_to_excel`, `protected_field_map`, `sync_excel_to_db`의 `guarded_fields`

15. **필드 값의 정본은 엑셀이다. 파이프라인 재빌드도 예외가 아니다.** 재빌드 전에 `prepare_ledger_for_rebuild()`(큐 반영 → 엑셀→DB 동기화)를 실행한다. `load_request_ledger`는 보호 ID와 tombstone만 DB 값을 쓴다. `updated_at != created_at` 같은 휴리스틱으로 DB를 우선하지 않는다(R3-06).

16. **ID 스탬핑은 명시 ID를 워크북 전체에서 먼저 모은 뒤 배정한다.** 명시 ID가 있는 시트에서는 DB에 남은 ID(삭제 tombstone, 다른 시트로 옮긴 항목)를 새 수기 행에 물려주지 않는다. 위치 기반 ID 유지는 명시 ID가 전혀 없는 레거시 시트에서만 한다(R3-05). 웹 등록 채번은 `year` 컬럼이 아니라 `REQ-YYYY-` 접두사로 한다(R3-08).
    - 위치: `scripts/extractors/ledger/ids.py` `plan_ledger_ids`, `scripts/services/ledger/ids.py` `next_ledger_id` (구: `ledger_parser.py` / `ledger_service.py` shim)

17. **엑셀 행 삭제 판정에 요청형태 값을 쓰지 않는다.** 운영 대장에서 '시스템'은 "국회 요구자료 시스템 접수"라는 업무 값이다. 삭제에서 빼야 하는 것은 "웹에서 등록했지만 엑셀에 한 번도 쓰이지 않은 행"이고, `ledger_history`의 `INSERT`/`EXCEL_APPLIED` 이력으로 판단한다(R3-10).

18. **대시보드 검색은 01·02 모드 모두 클라이언트 검색(`filterItems`)을 쓴다.** 서버 결과로 클라이언트 문법 처리를 건너뛰면 초성·OR·제외어가 0건이 되거나 반대로 동작한다(R3-03). 클라이언트 검색은 `full_markdown`/`answer_markdown` 전문을 대상으로 하며, 파생 문자열은 `searchCache`로 캐시한다. 원문 HTML은 `sanitizeHtml`(허용 목록 재조립)로만 삽입한다(R3-15).

19. **관리대장 마감 판정 규칙은 대시보드(`ledgerDueInfo`)와 서버(`ledger_due_state`)가 같아야 한다.** 제출·완료·업무설명·`[삭제]`는 완료, 마감일이 지나면 기한 경과, 7일 이내면 임박이다. 한쪽만 바꾸면 화면 필터와 `/api/ledger?due=` 결과가 달라진다. 두 구현은 `tests/test_audit_phase11_features.py`가 같은 픽스처로 검증한다.

20. **대시보드 JS에 설정값을 넣을 때는 `/* __APP_CONFIG_JSON__ */` 자리에 원본 JSON(`</` 이스케이프)으로만 넣는다.** HTML 자리표시자(`__HEADER_TITLE__` 등)는 이미 부서명이 붙고 HTML 이스케이프된 값이라 JS에 넣으면 제목에 부서명이 두 번 나오고 `&amp;`가 글자로 보인다. 렌더러는 `template_renderer.py`와 `build_universal_package.py` 두 곳이다.

## 배치파일·속도·대시보드 규칙 (후퇴 금지)

21. **배치파일(00~06)은 `scripts/write_all_bats.py`에서만 고친다.** 저장소 배치파일이 생성기 출력과 다르면 `test_repository_bats_match_generator`가 실패한다. Python 판정은 `where python`이 아니라 실제 실행과 버전 확인(`PY_DETECT`)으로 한다. Microsoft Store 바로가기가 `where`를 통과하기 때문이다. 06번은 원본 전용이다(`SOURCE_ONLY_BATS`).

22. **원문 해시는 `file_hash_cache.sha256_file()`로 구한다.** 파이프라인에서 같은 원문을 여러 번 해시하던 비용(754건·424MB × 5회)을 없앤 경로다. 크기·수정시각이 같고 수정 후 2초가 지난 파일만 캐시를 믿는다. `parse_all.py --force`는 `trust_stat=False`로 전부 다시 계산한다. 캐시 저장 실패는 무시한다(보조 자료).

23. **대시보드에서 서버·저장소를 바꾸는 동작은 `runExclusive`로 감싼다.** 등록 연타로 대장 중복 행이 생길 수 있었다. 오프라인 임시 등록은 전송 성공 건마다 `removeLocalLedgerIds`로 지운다. CSV 셀은 `csvCell`로 만든다(`=`/`+`/`-`/`@` 수식 주입 방지). 단순 안내는 `notify`, 되돌릴 수 없는 작업 확인만 `confirm`을 쓴다.

24. **엑셀 '제출' 칸이 비어 있고 제출일도 없으면 `미제출`이다.** 운영 대장은 제출한 행에 '제출'을 직접 적는다. 빈 칸을 '제출'로 채우면 진행 중 요구자료가 마감 경고에서 빠진다. 제출일만 있으면 `완료`. 위치: `scripts/extractors/ledger/load.py` `load_request_ledger` (구: `ledger_parser.py` shim). 테스트: `tests/test_audit_phase12_ledger_status.py`.

## 4회차 감사 불변조건 (후퇴 금지)

아래 25~31은 4회차 감사(`PROJECT_AUDIT.md` R4-xx)에서 조용한 되돌림·유실 경로로 확인한 것이다.

25. **마스터 대장 엑셀은 `extractors/ledger/discovery.py`의 `discover_master_excel` 한 곳에서만 고른다.** 웹→엑셀 반영(`find_master_excel`)과 엑셀→DB 파싱이 따로 탐색하면, 대장이 상위 워크스페이스에 있을 때 쓰기는 되고 읽기는 0건이 된다(R4-03). 이미 파일을 고른 호출자는 `load_request_ledger(..., excel_path=...)`로 **그 경로를 넘긴다.** 느슨한 패턴(`*대장*.xls*` 등)은 시스템 폴더에서만 쓴다. 원문 폴더에는 답변 엑셀이 섞여 있다.
26. **대장 헤더 별칭은 `extractors/ledger/dates.py`의 `LEDGER_COLUMN_ALIASES`가 정본이다.** 반영기(`ExcelApplyMixin.COLUMN_ALIASES`)는 이 표를 그대로 쓴다. 별칭을 한쪽에만 추가하면 그 헤더 대장에서 웹 수정이 엑셀에 안 적히고, 다음 동기화가 DB를 옛값으로 되돌린다(R4-04).
27. **02번 웹 서버는 시스템 폴더당 하나만 뜬다.** `run_server`가 `.web_server.lock`(PID·포트)을 잡고, 살아 있는 보유자가 있으면 새로 띄우지 않고 종료 코드 3으로 끝낸다. 포트만 바꿔 두 번째 서버를 띄우지 않는다. `ExcelSyncService.lock`은 프로세스 간 효력이 없다(R4-05). 위치: `scripts/web/instance_lock.py`.
28. **DB를 교체하는 경로는 모두 파이프라인 락을 잡는다.** `extract_and_build_db.main()`은 직접 실행해도 교체할 DB 폴더의 `PipelineGuard`를 획득한다(같은 프로세스가 이미 쥐었으면 재진입). README가 `--allow-shrink` 직접 실행을 안내하기 때문이다(R4-08).
29. **"읽었는데 아무것도 없음"을 성공으로 보고하지 않는다.** 대장 파일이 있고 DB에 엑셀 출처 항목이 있는데 0건을 읽으면 `sync_excel_to_db`는 `success: False, reason: "parsed_zero"`를 돌려준다(R4-07). 깨진 보류 큐는 조용히 비우지 않고 `.excel_pending_queue.corrupt.<시각>.json`으로 보존한 뒤 알린다(R4-11). 이 신호들과 서버 시작 동기화 오류는 `/api/sync`(`excel_last_sync`·`excel_queue_corrupt`·`excel_startup_error`)와 02번 화면 배너로 보인다.
30. **오프라인(01번, `file://`) 화면은 서버 대장 항목을 수정·삭제한 것처럼 보이지 않는다.** 이 브라우저의 임시 등록(`LOCAL-`)만 고칠 수 있다(`ledgerWriteBlockReason`). 서버에서 목록을 다시 받을 때는 `mergeLocalLedger`로 임시 등록분을 붙인다(R4-06, R4-15a).
31. **대시보드의 대장 수정은 `expected_updated_at`(읽은 시점의 `updated_at`)을 보낸다.** 서버는 다르면 `409 conflict`로 거절한다. 응답의 `updated_at`으로 화면 값을 갱신해야 다음 수정이 거짓 충돌이 되지 않는다. 이 값을 보내지 않는 API 호출자는 예전처럼 동작한다(4.1-1).

32. **투입 파일 하나 때문에 00번 전체를 멈추지 않는다.** 한글·Excel에서 열린 투입 파일은 옮기지 않고 건너뛰어 경고로 보고한다. 이동이 복사 후 원본 삭제에서 실패하면 이번 실행이 만든 사본을 되돌린다. 원문 연도 폴더에 반쪽 사본을 남기지 않는다(2026-09-17 운영 반영 중 발견). 위치: `add_documents_smart.copy_or_move_files`.
33. **대장 백업은 롤링 5본과 일별 스냅숏(`.ledger_backup/daily/`, 14일) 두 겹이다.** 롤링 정리 glob이 `daily/`를 건드리지 않게 하위 폴더를 유지한다.

## 검색·DB 고도화 불변조건 (후퇴 금지)

아래 34~40은 검색·DB 고도화 과정에서 확립한 불변조건이다. 대부분 **결과가 조용히 틀리던** 자리다.

34. **FTS 인덱스 정의는 `scripts/db/fts_spec.py` 한 곳이다.** 예전에는 같은 DDL이 `ensure_schema`(IF NOT EXISTS)와 `build_fts_and_indexes`(새로 생성), 그리고 테스트 픽스처까지 세 벌로 적혀 있었다. 한쪽만 고치면 신규 설치와 재빌드의 인덱스 구성이 갈라진다. 컬럼·가중치·트리거·드롭 SQL을 모두 이 표에서 만든다. 테스트 픽스처도 이 생성기를 쓴다.

35. **FTS는 정본 본문을 색인한다.** `documents_fts`는 `full_markdown`을, `qa_items_fts`는 `answer_markdown`을 넣는다. `answer_full_text`·`answer_full`은 **엑셀 3만 자 잘림본**이라 색인하지 않는다. 예전에는 잘림본을 색인해 02번 API와 05번 GUI가 본문 뒷부분에만 있는 낱말을 통째로 놓쳤다(운영 데이터에서 `아동청소년` 17건 중 12건만 나왔다). 같은 이유로 05번 GUI의 LIKE 검색과 상세 보기도 정본을 쓴다. 회귀: `tests/test_search_db_upgrade.py`의 `test_word_only_in_body_tail_is_found`.

36. **FTS 컬럼과 파이썬 대체 경로의 비교 필드는 같아야 한다.** 두 목록이 다르면 같은 검색어가 경로에 따라 다른 필드를 보고, 결과가 들쭉날쭉해진다. `_SEARCH_SPECS`는 비교 필드를 직접 적지 않고 `fts_spec`에서 가져온다. bm25 가중치 개수도 컬럼 수와 같아야 한다(다르면 질의가 실패한다). 회귀: `test_fts_columns_and_scan_fields_agree`.

37. **trigram은 3글자 미만을 색인하지 않는다.** 2글자 토큰을 MATCH에 넣으면 결과가 **항상** 0건이므로, `build_fts_match`는 짧은 토큰이 하나라도 있으면 빈 문자열을 돌려 대체 경로로 보낸다. 대체 경로는 SQL `LIKE` 선필터로 후보를 줄인 뒤 파이썬으로 평가한다. **선필터는 결과를 줄이면 안 된다** — 긍정 토큰으로만 좁히고, 제외어·구문은 파이썬이 뒤에 건다. **비교 대상은 검색 필드를 이어 붙여 공백을 뗀 블롭 하나다**(`_norm_expr`). 행당 한 번만 만들고(MATERIALIZED CTE) 모든 변형을 거기 비교한다. 이 블롭은 원시 일치를 이미 포함하므로 원시 LIKE 조건을 따로 두지 않는다. 짧은 필드에만 공백 제거를 걸었더니 본문에서 `국 회`처럼 갈라진 낱말을 놓쳐 운영 질의가 72건에서 70건이 됐다. **동의어가 켜져도 선필터를 끄지 않는다** — 변형을 모두 OR로 묶으면 여전히 상위집합이다(예전에는 꺼서 기본 경로가 늘 전수 스캔이었다). 회귀: `test_prefilter_matches_python_evaluation`(동의어 켬/끔 양쪽), `test_prefilter_finds_terms_split_by_whitespace_in_body`.

38. **동의어 사전의 정본은 `scripts/search_synonyms.py`다.** 대시보드 JS에는 조립할 때 `/* __SYNONYMS_JSON__ */` 자리로 주입한다(불변조건 20과 같은 방식). 예전에는 사전이 JS에만 있어 01번 화면은 동의어 검색이 되고 02번 API·05번 GUI는 안 됐다. 사전을 JS에 직접 적지 않는다.

39. **Q&A 답변 전문은 대시보드 페이로드에서 부모 본문의 위치 참조(`am_ref`)로 싣는다.** 답변은 부모 문서 `full_markdown` 안에 그대로 들어 있어, 그대로 실으면 같은 글자를 두 번 싣는다(운영 데이터 4,226건 전부 포함 확인). **이것은 전문 보존 정책 2항의 예외가 아니다** — 화면이 열릴 때 원문과 한 글자도 다르지 않게 되살리고, 위치를 못 찾으면 예전처럼 전문을 그대로 싣는다. 복원 규칙은 `00_boot.js`와 `DashboardRenderer.rehydrate_qa_bodies` **두 곳이 같아야 한다.** `verify_offline_dashboard_html`은 키 존재가 아니라 **복원 성공**을 검사한다. 회귀: `DashboardPayloadTests`.

40. **읽기 전용 연결은 퍼센트 인코딩 URI로 연다.** 이 저장소의 실제 경로에는 공백·쉼표·괄호·한글이 들어 있어, 인코딩하지 않은 `file:` URI는 연결이 실패한다. `extractors/ledger/workbook.py`의 `connect_readonly`를 쓴다. 점검·진단처럼 읽기만 하는 경로는 `immutable=True`로 `-wal`/`-shm`까지 건드리지 않는다(R4-01).

41. **`linked_doc_id`는 파생값이므로 재빌드 때마다 다시 맞춘다.** 가리키던 문서가 사라지면 그 값은 아무 뜻이 없다. `repair_broken_links`가 파이프라인 끝에서 다시 잇거나 비운다. 엑셀 컬럼이 아니라 DB 전용이라 여기서 고쳐도 엑셀 동기화가 되돌리지 않는다. **`auto_link_ledger_to_doc`은 `documents.linked_ledger_id`만 쓰고 대장 쪽 값은 호출자가 저장한다** — 이걸 빠뜨리면 "재연결했다"고 보고하고도 DB는 그대로다.

42. **점검·진단은 고칠 수 없는 사실을 경고로 띄우지 않는다.** 원문에 날짜가 없는 문서는 연도 필터에서 빠지는 것이 맞는 동작이고(전체 검색에는 나온다), 매번 경고로 남으면 진짜 경고가 묻힌다. `check_database`는 `warnings`(고쳐야 함)와 `notes`(사실)를 나눈다. 요구일자가 있는데 연도가 빈 경우만 경고다.

43. **점검은 현재 상태를 읽어야 한다.** `check_database`의 기본 연결은 `immutable`이 아니다. `immutable=1`은 `-wal`/`-shm`을 건드리지 않는 대신 아직 체크포인트되지 않은 WAL 프레임을 보지 못해, 방금 고친 내용을 못 보고 "이상 없음"을 돌려준다. 운영 파일을 절대 건드리면 안 되는 호출자만 `immutable=True`를 준다.

44. **새 컬럼은 기본 `CREATE TABLE`과 마이그레이션 **양쪽**에 넣는다.** 마이그레이션은 기존 DB를 올릴 때만 돌기 때문에, 마이그레이션에만 넣으면 새로 만든 DB에는 그 컬럼이 영영 생기지 않는다(`ledger_history.changed_fields`·`actor`가 그 상태였다). 신규 판정은 테이블을 만들기 **전에** 한다 — `ensure_schema`가 만든 뒤에 판정하면 새 DB도 늘 '기존 DB'가 되어 빌드마다 빈 테이블에 마이그레이션이 돌고, 진짜 마이그레이션이 일어난 순간이 그 소음에 묻힌다. 회귀: `test_fresh_schema_matches_migrated_schema`, `test_fresh_database_is_not_reported_as_migrated`.

**`/api/sync`에서 무거운 점검을 하지 않는다.** 대시보드가 주기적으로 부르는 경로다. 무결성 점검은 수백 MB를 훑으므로 `/api/db/health`로 필요할 때만 부르고, 파이프라인은 끝날 때마다 스스로 확인한다.

**스키마 버전은 `PRAGMA user_version`이 관리한다.** `scripts/db/migrations.py`의 `SCHEMA_VERSION`이 기대값이고, 낮으면 올리고 **높으면 거절한다**(구 버전 프로그램이 신 DB를 열어 새 컬럼을 지우는 경로를 막는다). 올리는 단계는 파괴적이지 않은 것만 자동 적용한다.

## 7회차 감사 불변조건 (후퇴 금지)

아래 45~51은 7회차 감사(2026-09-23, `PROJECT_AUDIT.md`)에서 **테스트가 실제 클라이언트 요청 모양을 흉내 내지 않아** 놓친 경로와, 대용량 DB 대응에서 정한 규칙이다.

45. **대장 수정의 "바뀐 필드"는 서버가 기존 값과 비교해 정한다.** 대시보드 편집 폼은 예전에 13개 필드를 모두 보냈고, 서버가 그 전부를 `_changed_fields`로 삼아 엑셀 행 전체를 다시 썼다. 보류 큐 재생 때 사용자가 엑셀에서 고친 다른 칸이 되돌아갔고, 파서가 마스킹한 값이 엑셀 원본 연락처를 덮었다. 비교는 DB에 저장되는 형태(마스킹·날짜 정규화 후)로 한다. 달라진 필드가 없으면 DB·이력·엑셀을 건드리지 않고 `unchanged: True`로 성공한다. 클라이언트도 모달을 연 시점과 달라진 필드만 보낸다(`ledgerDirtyFields`) — 2중 방어다.
    - 위치: `LedgerService._changed_update_fields`, `10_ledger_crud.js` `submitEditLedgerItemNow`. 회귀: `tests/test_audit_phase15_round7.py` `TestFullPayloadEdits`(**구 클라이언트의 13필드 페이로드로 검증한다. 부분 페이로드만으로 테스트하지 않는다**).
46. **편집 폼 드롭다운은 목록에 없는 현재 값을 보존한다.** `<select>`에 없는 값을 대입하면 `value`가 `''`가 된다(HTML 표준). `완료`·`업무설명` 같은 운영 값이 비고만 고친 저장에서 빈 값으로 넘어가 엑셀 '제출' 칸이 지워지고 `미제출`로 바뀌었다. `setSelectPreservingValue`로 임시 선택지를 붙인다. 서버는 기존 값이 있는 `status`를 빈 값으로 바꾸는 요청을 거절한다.
47. **웹 날짜 검증은 엑셀 파서와 같은 표기를 받고, 바뀐 날짜 필드에만 건다.** 파서가 읽지 못해 원문으로 남긴 날짜(`9월 중` 등)를 웹이 다시 보내도 다른 칸 수정을 막지 않는다. 읽을 수 있는 표기(`2026.9.10`, `9/10(수)` 등)는 `YYYY-MM-DD`로 저장한다. 정규화 정본은 `extractors/ledger/dates.py`의 `normalize_ledger_date`·`coerce_ledger_date`다.
48. **`_parsed_markdown` 배치 규칙은 `pipeline/parse/layout.py` 한 곳이다.** 파서·재배치 도구·배치 정리가 규칙을 따로 계산하면 같은 원문의 결과가 두 자리에 생기고 DB 문서가 중복된다. 00번은 파싱 전에 `consolidate_layout`으로 사본을 한 자리로 모은다(치운 사본은 `.maintenance_backup/`). DB 빌더는 실제 원문이 확인된 경우 같은 원문을 한 건으로 줄인다(최후 방어선).
49. **`<!-- source: … -->` 역추적 주석은 문서 전문이 아니다.** DB `full_markdown`·Q&A·엑셀에는 주석을 뗀 본문(`strip_source_header`)을 넣는다. 전문 보존 정책의 예외가 아니다 — 원문 내용은 한 글자도 빠지지 않는다.
50. **대시보드 전문이 크면 본문 블록(`COMPRESSED_BODY_n`)으로 나눠 싣는다. 빼는 것이 아니다.** 한 덩어리로 실으면 브라우저 문자열 한도(약 5억 자)에 걸려 대시보드가 열리지 않는다. 메타(`COMPRESSED_DATA`, `body_chunks`)로 화면을 먼저 띄우고 블록을 차례로 풀어 원래 자리에 되돌린다. 복원 규칙은 `00_boot.js`(`applyBodyChunk`)와 `PayloadMixin.decode_dashboard_payload` **두 곳이 같아야 한다.** `verify_offline_dashboard_html`은 블록 수와 복원 결과까지 검사한다. 본문이 오기 전 문서를 열면 `BODIES_READY`를 기다린다(요약으로 대신하지 않는다). 회귀: `tests/test_dashboard_scale.py`(실제 `00_boot.js`를 Node로 실행).
51. **대시보드 검색은 시간 조각으로 나눈 같은 평가 함수를 쓴다.** `render()`는 `filterItemsAsync`(= `filterItemsFromListAsync`)를 쓰고, 동기 `filterItemsFromList`와 결과가 같아야 한다(둘 다 `matchSearchItem`). 초성 변환은 초성 토큰이 있을 때만, 전문 비교는 토큰당 한 번(동의어 변형을 묶은 `bodyRe`)만 한다. JSON 캐시는 행 단위 스트리밍으로 쓴다(`db/json_export.py`) — 대장 한 건 수정마다 전문 전체를 메모리에 올리지 않는다.

## 대시보드 화면 규칙 (2026-10-06 UI/UX 정리, 후퇴 금지)

브라우저에서 실제로 재현한 흐름 문제에서 나온 규칙이다. 회귀: `tests/test_dashboard_uiux_refactor.py`.

52. **저장에 성공해 폼을 닫을 때는 `closeAddLedgerModal(true)`·`closeEditLedgerModal(true)`로 닫는다.** `15_ux.js`가 닫기 함수를 감싸 "적어 둔 내용이 있으면 확인"을 거는데, 저장 직후의 폼은 늘 '고친 상태'다. 인자 없이 닫으면 저장한 사용자에게 "저장하지 않고 닫을까요?"를 묻는다.
53. **모달을 열면서 `location.hash`에 대입하지 않는다.** `hashchange` → `applyDeepLink`가 같은 문서를 다시 열어 탭·스크롤을 되돌린다. 주소는 `history.replaceState`로만 바꾼다.
54. **알림(`#toast-stack`)을 화면 아래쪽에 두지 않는다.** 창 아래의 주요 버튼을 덮어 클릭을 막는다.
55. **`shell.html`에 모양을 정하는 인라인 `style`을 넣지 않는다.** JS가 켜고 끄는 `display:none`만 허용한다. 색·간격은 `styles.css`의 토큰과 클래스로 정한다.
56. **진행 상태 표시는 `ledgerStatusLabel`·`ledgerStatusClass`(`06_ledger_due.js`) 한 곳이다.** 빈 값은 `미제출`이다(불변조건 24). 카드·표·상세 창에서 따로 색을 정하지 않는다.
57. **탭별 정렬 선택지는 `MODE_SORTS`(`03_ui.js`)가 정본이다.** 탭을 바꿀 때 맞지 않는 정렬은 `updateModeUi`가 되돌린다. 선택(묶음 인쇄)·즐겨찾기는 답변서 전용이다.
58. **화면 문구는 쉬운 말을 쓴다.** `격리`·`보류 큐`·`동기화`·`SQLite` 같은 내부 용어를 사용자 문구에 넣지 않는다. 대응표는 README 「화면 용어」. 테스트가 문구를 단정하는 곳(`dueBadgeHtml`, `excelStatusMessage`)은 문구를 바꿀 때 함께 고친다.

대시보드 화면을 고친 뒤에는 조립 캐시를 다시 만들고, 가능하면 가짜 데이터로 띄운 화면을 브라우저에서 눌러 본다(Node 하네스는 저장 직후 확인창·탭 되돌림 같은 흐름 문제를 잡지 못했다).

## 테스트 규칙

**소스 문자열을 grep 하는 것으로 동작을 검증했다고 하지 않는다.** `test_gui_reports_pipeline_failure`가 `'result_data.get("success", True)' in code`만 확인한 탓에, 그 코드가 `UnboundLocalError`로 실행조차 되지 않는 상태가 오래 가려져 있었다. 실행하거나, 최소한 `symtable`/`compile`/`ast` 수준의 정적 검증을 쓴다. 대시보드 JS는 템플릿에서 함수를 떼어 Node로 실행한다(`tests/test_audit_phase10_dashboard.py`의 `extract_js`/`run_node`).

**테스트는 운영 데이터에 쓰지 않는다. (후퇴 금지)** 3회차 감사에서 테스트가 운영 마스터 엑셀·DB·보류 큐에 테스트 데이터를 기록해 실제 대장이 오염된 것이 확인됐다(R3-01).
- `LedgerService`/`ExcelSyncService`를 만들 때 `base_dir`에 임시 디렉터리를 준다. `web_server`를 쓰는 테스트는 `DB_PATH`·`JSON_PATH`와 함께 **`BASE_DIR`도** 바꾸고 되돌린다(`init_db()`는 보류 큐 반영과 엑셀→DB 동기화까지 한다).
- 운영 DB를 읽어야 하면 `ledger_parser.connect_readonly(path, immutable=True)`로 연다. 쓰기 연결을 닫을 때의 WAL 체크포인트도 운영 파일을 바꾸고, `mode=ro`만으로도 `-shm`/`-wal`이 바뀐다(R4-01). `immutable` 연결은 `journal_mode`를 `delete`로 보고하므로 WAL 여부는 파일 헤더 18·19바이트로 확인한다.
- `load_request_ledger`를 테스트에서 부를 때는 `db_path`·`system_dir`를 임시 경로로 **명시한다.** 경로 해석은 root_dir 기준이지만(R4-02), 명시하지 않으면 운영 설정·대장을 읽는 실수가 다시 생긴다.
- `tests/__init__.py`가 `sqlite3.connect`를 감싸 운영 `data_requests.db` 연결(immutable 제외)을 기록하고, `test_zzz_production_isolation`이 그 목록이 비었는지 본다. 이 감시를 지우지 않는다.
- 도구 실행 환경에 따라 표준입력 핸들이 없으면 `subprocess` 테스트가 `WinError 6`으로 실패한다. 이때는 `cmd /c "python -m pytest tests -q < NUL"`처럼 표준입력을 연결해 실행한다. 새 테스트의 `subprocess` 호출에는 `stdin=subprocess.DEVNULL`을 준다.
- 운영 대장을 Excel로 열어 두고 저장하면 격리 테스트가 그 저장을 "테스트가 바꿨다"로 잡는다. 실패하면 먼저 `.ledger_backup/`에 새 백업이 생겼는지 본다. 시스템 저장은 항상 백업을 남기므로, 백업이 없으면 사용자 저장이다.
- 쓰기 가드는 시스템 폴더와 함께 연도 폴더(20xx)가 있는 상위 워크스페이스(원문 폴더)도 보호한다. 원문 폴더에 파일을 만드는 코드(`add_documents_smart.copy_or_move_files` 등)도 쓰기 전에 확인한다.
- 쓰기 지점에서는 `write_guard.ensure_writable()`를 호출한다. 이 예외는 `PermissionError`가 아니다(엑셀 잠김으로 오인해 운영 큐에 다시 쓰지 않게 하기 위해서다).
- `tests/test_zzz_production_isolation.py`가 스위트 전후 운영 데이터 파일 지문을 비교한다. 이름을 바꾸거나 지우지 않는다(파일명 순서상 마지막에 실행돼야 한다).

회귀 테스트: `tests/test_dashboard_uiux_refactor.py`(화면 정리), `tests/test_audit_phase15_round7.py`, `tests/test_dashboard_scale.py`(7회차), `tests/test_audit_phase10_ledger.py`, `tests/test_audit_phase10_dashboard.py`(3회차), `tests/test_audit_phase9_fixes.py`, `tests/test_audit_phase8_fixes.py`, `tests/test_excel_bidirectional_sync.py`, `tests/test_audit_remediations.py`.
저장소별 정본 규칙은 README 「저장소별 정본(Source of Truth) 규칙」을 따른다.
