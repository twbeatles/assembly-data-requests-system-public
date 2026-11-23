# 취합 포털 외부 연동 및 정식 운영 설계

외부 회사 포털·ERP·전자결재에서 요구 건을 접수하고 상태를 조회할 수 있다. 외부 서버는 연동 전용 키를 사용하고, 취합팀은 접수 건을 검토한 뒤 내부 부서를 배정한다. 사용자 로그인과 연동 키는 별개이며, 연동 응답으로 받는 값은 외부 ID, 상태, 대외 마감일이다.

## 외부 회사 포털 연결

| 기능 | 호출 | 권한과 응답 |
| --- | --- | --- |
| 접수 | `POST /integrations/v1/requests` | 접수 권한이 있는 연동만 호출한다. 서버에 설정된 취합팀으로 접수된다. 최초 접수는 201, 같은 외부 ID로 같은 내용을 재전송하면 200, 내용이 달라지면 409다. |
| 상태 조회 | `GET /integrations/v1/requests/{external_id}` | 해당 연동이 접수한 건만 조회한다. 같은 취합팀에 연결된 별도 연동과 다른 취합팀은 각각 독립 범위를 사용한다. |

공식 호출 규격은 [OpenAPI 계약](collection-portal-openapi.yaml)에 있다. 요청 필드는 `external_id`, `title`, `requester`, `instructions`, `due` 다섯 개다. 연동 서버가 생성한 식별자를 외부 ID로 쓰고, 날짜는 `YYYY-MM-DD`로 보낸다. 취합팀이나 부서·권한·첨부·대장 ID와 같은 내부 데이터는 회사 포털에서 지정할 수 없다. 상태는 `received`(접수·배정 대기), `collecting`(취합 중), `finalized`(최종 확정), `sent`(대외 제출 확인) 순서로 바뀔 수 있으며, 책임자가 취합을 재개하면 상태가 돌아갈 수 있다.

1. 보호된 운영 호스트에서 `integration-config erp01 collection --intake`로 연동과 취합팀을 연결한다. 같은 명령에서 `--intake`를 빼면 해당 연동의 기존 접수 건 조회만 허용된다.
2. `integration-key erp01 --days 30`으로 키를 한 번 발급한다. 위 명령의 앞에는 `python -m scripts.collection_portal --db $portalDb`를 붙인다. 키는 발급 콘솔에서만 출력되며, 회사 서버의 비밀 저장소에 등록한다. 연동 서버는 `Authorization: Bearer <토큰>` 헤더로 키를 보낸다.
3. 운영 포털은 `serve --https-origin https://collection.example.org --enable-integrations`로 실행한다. HTTPS 인입을 담당하는 프록시는 정해진 Host를 보존하고 루프백 서버로만 전달한다. 회사 연동 서버에 허용할 외부 경로는 `/integrations/v1/`로 제한하고, 직원용 화면 경로에는 별도의 접속 정책을 적용한다. 로컬 합성자료 연동 테스트에는 `serve --local-test-integrations`를 사용한다.
4. 키 교체 시 새 키를 발급한 뒤 회사 서버 설정을 전환하고, `integration-revoke-key erp01 <키 ID>`로 옛 키를 폐기한다. 사고 시 `integration-disable erp01`로 연동 전체를 중지한다. `integration-list`에 키 ID, 유효기간, 폐기 상태를 표시한다. 토큰 원문은 표시하지 않는다.

서버 연동 호출에 브라우저 쿠키나 Origin은 사용하지 않는다. 웹 사이트 로그인은 별도 사용자 세션을 이용한다. 회사 포털에서 필요한 경우 직원에게 취합 포털 링크를 제공하고, 외부의 브라우저 스크립트에 연동 키를 넣지 않는다. 429·503 또는 응답 유실에는 지연 후 같은 외부 ID와 내용으로 재시도한다. 400·401·403·409는 연동 담당자가 원인과 입력을 확인한다. 문의 시 응답의 `X-Request-ID`를 기록하면 서버 로그와 연결할 수 있다. [OWASP REST 보안 지침](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html) · [OWASP 비밀 관리 지침](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)

## 권한 및 보안 감사

| 점검 대상 | 이번에 적용한 제어 | 배포 환경에서 확인할 항목 |
| --- | --- | --- |
| 사용자와 연동 주체 | 사용자 쿠키와 베어러 키를 분리했다. 요청과 파일 조회의 팀·부서 경계를 유지하고, 연동 키는 자기 접수 건의 최소 상태만 읽는다. | 외부 회사 서버에서 내부 사용자용 경로가 차단되는지 실제 프록시에서 확인한다. |
| 결재 충돌 | 제출 부서와 취합 역할을 함께 가진 사용자라도 본인 제출본 승인은 거부한다. | 제출 담당자와 승인 담당자 계정을 나누고, 직무별 권한표와 대리·휴가 시 승인 절차를 정한다. |
| 키 수명과 회수 | 키는 해시로 저장하며, 만료·폐기·연동 중지와 접수 권한을 요청마다 재검사한다. 중복 접수는 외부 ID와 요청 내용 지문으로 확인한다. | 팀별 서비스 계정 소유자와 비밀 저장소를 지정하고, 유효기간 만료와 교체 실패를 모니터링한다. |
| 브라우저 보안과 처리량 | HTTPS 모드에서 HSTS와 `__Host-` 보안 쿠키를 적용한다. 대용량 폼을 읽기 전에 세션을 확인하고, 작업 시작 시 세션을 다시 확인한다. 로그인·인증된 연동의 빈도와 HTTP 동시 요청 수를 제한한다. | 프록시에 출처별 요청 제한·본문 제한·전송 시간 제한을 둔다. TLS 인증서와 갱신, 로그의 인증 헤더 제거, 업로드 악성코드 검사 절차를 검증한다. |
| 감사 추적 | 포털 DB에 권한 변경·키 발급·폐기·로그인·업무 상태 변경을 남긴다. 승인된 다운로드 요청과 인증된 연동 성공은 서버 로그에 대상 종류·주체를 기록한다. | 서버 로그와 `audit` 출력을 접근이 제한된 중앙 저장소로 수집한다. 다운로드 이벤트는 권한 승인 시점이며 실제 파일 수신 완료와 구분한다. |

다른 팀 또는 다른 연동의 건에 대한 조회, 쿠키와 연동 키의 혼용, 키 폐기·만료, 접수 권한 회수, 중복 접수와 본인 승인 차단을 회귀 테스트로 관리한다. DB와 프록시에 직접 접근할 수 있는 계정, 회사 직원의 IdP 계정, 외부 연동을 운영하는 서버 계정은 각자 다른 운영 경계로 관리한다. [OWASP 접근 권한 지침](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) · [OWASP 로그 지침](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)

## 백업, 복구, 감사 운영

포털의 SQLite DB 한 파일에는 본문, 한글 원본, 계정과 키 해시, 상태 이력이 있다. 보호된 로컬 볼륨에 원본 DB와 백업 임시 파일을 두고, 승인된 운영 계정으로 실행한다. 회사의 보관 규정에 맞춰 접근이 제한되고 암호화된 별도 보관소에 사본을 보관한다.

```powershell
python -m scripts.collection_portal --db $portalDb check
python -m scripts.collection_portal --db $portalDb backup $backupPath
python -m scripts.collection_portal --db $backupPath check
python -m scripts.collection_portal --db $portalDb audit --after 0 --limit 1000
```

`backup`은 SQLite 온라인 백업으로 일관된 사본을 만들고 무결성·외래 키를 확인한 뒤 지정 경로에 공개한다. 같은 이름의 백업을 덮어쓰지 않는다. 백업 대상 폴더는 하드 링크를 지원해야 한다. `audit`는 다음 정기 수집 때 마지막으로 수집한 ID를 `--after`에 넣어 이어서 받을 수 있다. 사유에 업무 정보가 포함될 수 있어 감사 출력도 보호한다. [SQLite 온라인 백업 API](https://www.sqlite.org/backup.html)

복구 시험 시 백업을 새 DB 경로로 복제하고 `check`를 실행한다. 오래된 사본에서 이전 토큰이나 세션이 되살아나지 않도록 연결 전에 복구 DB에 `invalidate-access`를 실행한다. 포털 권한과 활성 계정은 복구 시점 이후의 인사·회수 기록을 대조해 다시 반영한다. 그다음 키를 재발급하고, 샘플 요청·최종본과 양식의 해시를 대조한 뒤 새 DB에서 서비스를 연다. RPO와 RTO는 회사의 업무 기준으로 정하고 실제 복구 시간을 기록한다.

## 시범에서 정식 운영으로 넘어가는 기준

| 관점 | 설계 및 완료 기준 |
| --- | --- |
| 제출 부서 | 새 배정·보완 요청은 포털 공지함에서 확인하고 해당 건으로 이동한다. 회사 메일·업무 메신저까지 통지하는 경우에는 승인된 연동을 선정하고, 메시지에는 권한 검사되는 포털 링크를 넣는다. |
| 취합팀 | 외부 요청의 진위를 검토한 뒤 배정한다. HWP·HWPX 실사용 서식, 해당 없음, 지연, 담당자 부재, 보완과 확정 취소에 대해 사용자 인수 시험을 한다. 대외 제출 기록과 대장 상태는 기존 업무 절차에서 대조한다. |
| 회사 계정과 인사 | 실제 IdP의 OIDC SSO와 MFA를 사용자 로그인에 연결할 때는 발급자·서명·대상·만료를 검증하고, 안정적인 발급자·계정 ID 조합과 포털 권한을 매핑한다. 이메일·요청 헤더를 권한의 근거로 쓰지 않는다. 인사 발령과 퇴직에 따른 세션·권한 회수 시간도 검증한다. [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html) |
| 회사 연동과 경계 | 기관이 OAuth 기반 서비스 인증을 요구하면 연동 키 대신 실제 인가 서버의 대상 제한 토큰과 최소 스코프를 검증한다. 프록시 차단·재시도·키 교체·장애 대응을 양사 계약에 포함하고, 외부 문서 전송은 별도 문서 정책과 검역 경계를 정한 뒤 확장한다. [OAuth 2.0 보안 권고 RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html) |
| 인프라·자료 보호 | 단일 서비스 인스턴스와 로컬 SQLite로 실제 동시 사용자·첨부 트래픽을 시험한다. 부하·장애·장기 보관 요구가 이 구성을 넘으면 공유 DB와 전용 파일 저장소를 검토한다. 본문·파일의 보관 만료, 법적 보존, 백업 폐기, 감사 수집 및 장애 통보 담당자를 확정한다. |
| 정식 전환 승인 | 실제 IdP·네트워크 경로·프록시·한글 환경의 검증, 접근 권한 매트릭스 재검토, 운영 DB 복구 리허설, 개인정보 및 반출 검토 결과를 회사 책임자가 확인한다. |

이 저장소에서 제공하는 연동은 회사 서버가 호출하는 접수·상태 API다. 사내 IdP 등록, 회사망 배포, 실사용 문서 검사와 기관별 부하 시험은 각 운영 환경에 대한 접근이 있어야 완료할 수 있다.

## GitHub 구현 비교와 반영

| 리포지토리와 검토 지점 | 취합 포털에 적용한 결정 |
| --- | --- |
| Apache Casbin의 도메인 RBAC 모델에서 요청 자원과 정책의 도메인이 일치해야 한다는 조건 | 계정 ID만으로 공지 접근을 허용하지 않는다. 취합팀 공지는 대상 요구 건의 팀과 현재 팀 권한을, 부서 공지는 대상 배정과 현재 부서 권한을 함께 확인한다. [도메인 RBAC 모델](https://github.com/apache/casbin/blob/master/examples/rbac_with_domains_model.conf) |
| OpenFGA의 조직·부모 폴더·문서 관계에 따른 권한과 허용·거부 예제 | 공지 → 요구 건 또는 배정의 관계를 DB에서 검증한다. 공지함과 검색에 동일한 대상 조건을 적용하고, 이전 링크로 이동할 때 관련 화면 권한도 다시 검사한다. 다중 시스템에서 권한을 공동 관리할 필요가 생기면 외부 정책 엔진을 별도로 평가한다. [관계 기반 모델 예제와 테스트](https://github.com/openfga/sample-stores/blob/main/stores/modeling-guide/step-10-fine-grained-api-access.fga.yaml) |
| Nextcloud 알림의 사용자·업무 객체 식별과 관련 작업 연결 | 공지를 원본 업무 변경과 한 트랜잭션에 저장한다. 공지에서 현재 권한으로 열 수 있는 요청이나 배정 화면으로 이동한다. 업무 메일 전송은 운영 환경의 승인된 통지 채널을 이용할 때 확장한다. [알림 발행과 처리 절차](https://github.com/nextcloud/notifications/blob/master/docs/notification-workflow.md) |
| Zulip의 사용자별 메시지 읽음 플래그와 변경 코드 | 읽음은 공지와 계정의 조합으로 저장한다. 여러 사람이 같은 팀 공지를 받더라도 한 사람의 확인은 다른 사람에게 영향을 주지 않는다. 공지 본문 검색 및 읽음 처리는 동일한 접근 조건을 사용한다. [사용자별 메시지 플래그 처리](https://github.com/zulip/zulip/blob/main/zerver/actions/message_flags.py) |
