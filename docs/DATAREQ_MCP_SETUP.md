# datareq CLI·MCP 설치·연결·권한·장애 처리

## 설치

```powershell
# 기본 (조회만, 추가 설치 없음)
python scripts/datareq_cli.py doctor --json

# MCP 클라이언트 연결용 (선택 의존성)
pip install -r requirements-agent.txt
```

## 연결

**Claude Code (2026-10-08 연결 실측 통과)**

```powershell
claude mcp add datareq python C:\path\to\repo\scripts\datareq_mcp.py -e DATAREQ_DATA_ROOT=C:\path\to\data
claude mcp get datareq   # Status: Connected 확인
```

- 실측 결과: 격리 설정(`CLAUDE_CONFIG_DIR` 임시 경로, 사용자 설정 무변경)에서
  `mcp add` → `mcp get` **Status: Connected**, `mcp list` health-check 통과.
  주의: `-e`는 가변인자라 `--` 뒤가 아니라 `python <스크립트>` 다음에 둔다
  (`--` 뒤에 두면 `commandOrUrl` 누락 오류).
- Live 도구 호출(`claude -p`)은 사용자 인증·쿼터가 필요해 수행하지 않았다.

**Codex (config 예시, 버전에 따라 키 검증 필요)**

```toml
[mcp_servers.datareq]
command = "python"
args = ["C:\\path\\to\\repo\\scripts\\datareq_mcp.py"]
```

- 상태: 미검증 (차단 사유: 이 PC에 Codex CLI 미설치. 위 Claude 실측 + 아래 SDK
  프로토콜 테스트로 2종 smoke는 충족하고, Codex 실연결은 CLI 설치 후
  `docs/ADVANCEMENT_BASELINE.md` 6절에 추가 기록한다)

**공식 SDK 클라이언트 (자동 테스트, 매 실행 검증)**

- `tests/test_agent_mcp_readonly.py::test_stdio_protocol_roundtrip_with_sdk_client`가
  실제 서버 프로세스를 stdio로 띄워 initialize → tools/list → `datareq_search`
  호출까지 검증한다. SDK stdio 클라이언트는 안전 허용 목록 환경변수만 상속하므로
  `DATAREQ_DATA_ROOT`는 서버 `env`에 명시해야 한다.

**데이터 루트 지정**

```powershell
$env:DATAREQ_DATA_ROOT = "C:\path\to\repo"
python scripts/datareq_mcp.py
```

미지정 시 시스템 폴더를 쓴다. 허용 루트 밖 경로는 `policy_denied`로 거절된다.

## 권한 프로파일

| 프로파일 | 범위 | 비고 |
|---|---|---|
| `reader` | 검색·발췌·대장 조회·집계·근거 패키지 (기본) | 연락처 마스킹 |
| `analyst` | `reader`와 같은 읽기 전용 범위 | 연락처 마스킹 |
| `operator` | v1 미제공 | 승인 체계 확정 후 P4에서 검토 |

v1은 읽기 전용만 제공하므로 `reader`·`analyst`에 프로파일별 차등이 없다. 집계·근거
패키지도 `reader`가 호출할 수 있다. `DATAREQ_PROFILE`에 `operator`를 줘도 v1은
`reader`로 동작한다.

## 장애 처리

| 증상 | 의미 | 할 일 |
|---|---|---|
| `dataset_unavailable` (exit 3) | DB 없음·스키마 불완전 | 00번 파이프라인으로 DB 구축 후 재시도. 자동 생성·재빌드 안 함 |
| `index_error` (exit 3) | 손상 DB·FTS 어긋남 | `python tools/db_report.py --rebuild-fts`, 백업 확인 |
| `policy_denied` (exit 5) | 허용 루트 밖·상한 초과 | `--data-root`·`max_chars`·`limit` 확인 |
| `invalid_input` (exit 2) | 잘못된 ID·필터 | ID·연도 형식 확인 |
| `no_results` | 검색 0건 | 동의어·연도 필터·오타 확인 (`/api/search/insights`는 02번 서버에서) |

## 보안 주의

- MCP를 쓴다고 자료를 LLM에 보내도 된다는 뜻이 아니다. 인용 발췌의 외부 모델
  전달은 호스트·모델·조직 보안정책 확인 후 opt-in이다.
- 배포 HTML·DB에는 전문이 포함된다. 공개 업로드·클라우드 LLM 전달은 별도 승인 대상이다.
- 첫 버전 MCP는 사용자 PC 로컬 stdio만 지원한다. 내부망·인터넷 공개 금지.
