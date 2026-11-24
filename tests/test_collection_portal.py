"""Exercise portal authorization and state transitions through its HTTP entry point."""

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
from http.client import HTTPConnection
from io import BytesIO
from struct import pack, pack_into
from threading import Thread
from time import time
from urllib.parse import urlencode
from zipfile import ZipFile

import pytest

from scripts.collection_portal import documents, integrations, operations, store
from scripts.collection_portal.__main__ import demo, set_account, set_grant
from scripts.collection_portal.http import PortalServer


class Client:
    def __init__(self, server, account, password):
        self.server = server
        self.cookie = ""
        _, page, _ = self.send("GET", "/")
        status, _, _ = self.send(
            "POST", "/login", page, login=account, password=password
        )
        assert status == 303

    def send(self, method, path, page="", *, origin=None, upload=None, **fields):
        headers = {"Cookie": self.cookie}
        body = None
        if method == "POST":
            headers["Origin"] = self.server.origin if origin is None else origin
            if page:
                fields["csrf"] = field(page, "csrf")
            if upload:
                name, data = upload
                boundary = "abcd0123985"
                headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
                parts = []
                for key, value in fields.items():
                    parts.append(
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"'
                        f"\r\n\r\n{value}\r\n".encode()
                    )
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"'
                    "\r\nContent-Type: text/plain\r\n\r\n".encode()
                    + data
                    + b"\r\n"
                )
                body = b"".join(parts) + f"--{boundary}--\r\n".encode()
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                body = urlencode(fields)
        host, port = self.server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        try:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            outgoing = response.getheader("Set-Cookie")
            if outgoing:
                self.cookie = outgoing.split(";", 1)[0]
            content = response.read()
            if "text/" in response.getheader("Content-Type", ""):
                content = content.decode("utf-8")
            return response.status, content, response.getheader("Location")
        finally:
            conn.close()

    def get(self, path):
        status, page, _ = self.send("GET", path)
        assert status == 200
        return page


def field(page, name):
    return re.search(rf'name="{name}" value="([^"]*)"', page)[1]


@pytest.fixture
def pilot(tmp_path):
    db = tmp_path / "collection.db"
    store.initialize(db)
    with store.connect(db, write=True) as conn:
        users = dict(demo(conn))
        users["other"] = "other-pass-for-isolation"
        set_account(conn, "other", users["other"])
        conn.execute("INSERT INTO scopes VALUES ('other-team','별도 취합팀','team')")
        set_grant(conn, "other", "other-team", "lead", True)
    with PortalServer(("127.0.0.1", 0), db, enable_integrations=True) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield (
                db,
                {
                    name: Client(server, name, password)
                    for name, password in users.items()
                },
            )
        finally:
            server.shutdown()
            worker.join()


def test_portal_submission_review_export_and_scope_revocation(pilot):
    db, users = pilot
    planning, ops, reviewer, lead, other = (
        users[x] for x in ("planning", "operations", "reviewer", "lead", "other")
    )
    assert "운영부서" not in planning.get("/requests/1")
    assert other.send("GET", "/requests/1")[0] == 404
    assert "부서별 업무 추진 현황 제출" not in other.get(
        "/?" + urlencode({"q": "업무"})
    )
    assert planning.send("GET", "/assignments/2")[0] == 404
    draft = planning.get("/assignments/1")
    version = field(draft, "version")
    assert (
        planning.send(
            "POST",
            "/assignments/1/draft",
            draft,
            version=version,
            body="부서 비공개 초안",
            origin="http://unrelated.example",
        )[0]
        == 403
    )
    assert (
        planning.send(
            "POST",
            "/assignments/1/draft",
            draft,
            version=version,
            body="부서 비공개 초안",
            upload=("계획.txt", "첨부 보존".encode()),
        )[0]
        == 303
    )
    assert "부서 비공개 초안" not in reviewer.get("/assignments/1")
    assert reviewer.send("GET", "/attachments/1")[0] == 404
    assert planning.send("GET", "/attachments/1")[1] == "첨부 보존".encode()
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            draft,
            contact="담당자 02-000-0000",
            version=version,
            body="오래된 요청",
        )[0]
        == 409
    )
    draft = planning.get("/assignments/1")
    assert "계획.txt" in draft
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            draft,
            contact="담당자 02-000-0000",
            version=field(draft, "version"),
            body="계획 1판 <script>",
        )[0]
        == 303
    )
    assert ops.send("GET", "/attachments/1")[0] == 404
    review_page = reviewer.get("/assignments/1")
    assert "계획 1판 &lt;script&gt;" in review_page
    due = (store.date.fromisoformat(store.today()) + timedelta(days=2)).isoformat()
    assert (
        reviewer.send(
            "POST",
            "/assignments/1/return",
            review_page,
            version=field(review_page, "version"),
            note="기준일 추가",
            due=due,
        )[0]
        == 303
    )
    draft = planning.get("/assignments/1")
    assert "기준일 추가" in draft and "계획.txt" in draft
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            draft,
            contact="담당자 02-000-0000",
            version=field(draft, "version"),
            body="계획 2판",
        )[0]
        == 303
    )
    draft = ops.get("/assignments/2")
    assert (
        ops.send(
            "POST",
            "/assignments/2/submit",
            draft,
            contact="담당자 02-000-0000",
            version=field(draft, "version"),
            body="해당 업무 없음",
            na="1",
        )[0]
        == 303
    )
    for assignment_id in (1, 2):
        path = f"/assignments/{assignment_id}"
        review_page = reviewer.get(path)
        assert (
            reviewer.send(
                "POST",
                path + "/approve",
                review_page,
                version=field(review_page, "version"),
            )[0]
            == 303
        )
    request = reviewer.get("/requests/1")
    assert (
        reviewer.send(
            "POST", "/requests/1/finalize", request, version=field(request, "version")
        )[0]
        == 404
    )
    assert (
        reviewer.send(
            "POST",
            "/requests/1/prepare",
            request,
            version=field(request, "version"),
            collection=field(request, "collection"),
            body="공동 취합 초안",
        )[0]
        == 303
    )
    request = lead.get("/requests/1")
    assert "공동 취합 초안" in request
    assert (
        lead.send(
            "POST",
            "/requests/1/finalize",
            request,
            version=field(request, "version"),
            collection=field(request, "collection"),
            body="승인한 최종 답변",
        )[0]
        == 303
    )
    assert planning.send("GET", "/finals/1/download")[0] == 404
    assert reviewer.send("GET", "/finals/1/download")[0] == 404
    status, data, _ = lead.send("GET", "/finals/1/download")
    assert status == 200
    with ZipFile(BytesIO(data)) as archive:
        assert "승인한 최종 답변" in archive.read("최종답변.txt").decode()
        assert "기획부서 제출 2판" in archive.read("취합근거.txt").decode()
        assert archive.read("첨부/2-계획.txt") == "첨부 보존".encode()
    request = lead.get("/requests/1")
    assert (
        lead.send(
            "POST",
            "/requests/1/sent",
            request,
            version=field(request, "version"),
            sent_on=store.today(),
        )[0]
        == 303
    )
    request = lead.get("/requests/1")
    assert (
        lead.send(
            "POST",
            "/requests/1/ledger",
            request,
            version=field(request, "version"),
            ledger_id="REQ-2026-001",
        )[0]
        == 303
    )
    request = lead.get("/requests/1")
    assert "대장 반영 수동 확인 완료" in request
    assert (
        lead.send(
            "POST",
            "/requests/1/reopen",
            request,
            version=field(request, "version"),
            note="이전 판 보존",
        )[0]
        == 303
    )
    assert lead.send("GET", "/finals/1/download")[0] == 200
    with store.connect(db, write=True) as conn:
        set_grant(conn, "planning", "planning", "department", False)
    assert planning.send("GET", "/requests/1")[0] == 404
    assert planning.send("GET", "/attachments/1")[0] == 404


def test_existing_database_is_rejected(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE request_ledger (id INTEGER)")
    with pytest.raises(store.PortalError):
        store.initialize(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == [
            ("request_ledger",)
        ]


def sample_hwp(signature=documents.HWP_SIGNATURE):
    """Minimal Compound File with a 256-byte HWP 5 FileHeader mini stream."""
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = documents.HWP_MAGIC
    pack_into("<5H", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    pack_into("<9I", header, 40, 0, 1, 1, 0, 4096, 2, 1, end, 0)
    pack_into("<109I", header, 76, 0, *([free] * 108))
    fat = pack("<128I", fat_sector, end, end, end, *([free] * 124))
    mini_fat = pack("<128I", 1, 2, 3, end, end, *([free] * 123))

    def entry(name, kind, *, left=free, child=free, sector=end, size=0):
        encoded = (name + "\0").encode("utf-16-le")
        return pack(
            "<64sHBBIII16sIQQIII",
            encoded,
            len(encoded),
            kind,
            1,
            left,
            free,
            child,
            bytes(16),
            0,
            0,
            0,
            sector,
            size,
            0,
        )

    directory = (
        entry("Root Entry", 5, child=1, sector=3, size=320)
        + entry("FileHeader", 2, left=2, sector=0, size=256)
        + entry("DocInfo", 2, sector=4, size=1)
        + bytes(128)
    )
    mini_stream = (signature + bytes((7, 0, 0, 5))).ljust(512, b"\0")
    return bytes(header) + fat + directory + mini_fat + mini_stream


def sample_hwpx(text, *, bad_name=None):
    out = BytesIO()
    with ZipFile(out, "w") as package:
        package.writestr("mimetype", documents.HWPX_MIME)
        package.writestr("META-INF/container.xml", "<container/>")
        package.writestr("Contents/content.hpf", "<package/>")
        package.writestr("Contents/header.xml", "<head/>")
        package.writestr("Contents/section0.xml", f"<section>{text}</section>")
        if bad_name:
            package.writestr(bad_name, "path check")
    return out.getvalue()


def test_hancom_validation_distinguishes_same_container_formats():
    for name, payload in (
        ("원본.HWP", sample_hwp()),
        ("원본.hwpx", sample_hwpx("원문")),
    ):
        documents.validate_file(name, payload)
    with pytest.raises(documents.InvalidDocument):
        documents.validate_file(
            "가짜.hwp", sample_hwp(b"Different Compound File".ljust(32, b"\0"))
        )
    with pytest.raises(documents.InvalidDocument):
        documents.validate_file("가짜.hwp", documents.HWP_MAGIC)
    with pytest.raises(documents.InvalidDocument):
        documents.validate_file("가짜.hwpx", sample_hwp())
    with pytest.raises(documents.InvalidDocument):
        documents.validate_file("잘린.hwpx", sample_hwpx("원문")[:-20])
    for invalid in ("../outside", "Contents/SECTION0.xml"):
        with pytest.raises(documents.InvalidDocument):
            documents.validate_file(
                "잘못된.hwpx", sample_hwpx("원문", bad_name=invalid)
            )
    windows_separator = sample_hwpx("원문", bad_name="Contents/outside").replace(
        b"Contents/outside", b"Contents\\outside"
    )
    with pytest.raises(documents.InvalidDocument):
        documents.validate_file("잘못된.hwpx", windows_separator)


def test_hancom_templates_submissions_and_finals_preserve_original_bytes(pilot):
    _, users = pilot
    lead, reviewer, planning, operations, other = (
        users[name] for name in ("lead", "reviewer", "planning", "operations", "other")
    )
    template = sample_hwpx("기관 서식")
    submitted = sample_hwp()
    original_final = sample_hwpx("첫 최종본")
    due = (store.date.fromisoformat(store.today()) + timedelta(days=2)).isoformat()
    status, _, location = lead.send(
        "POST",
        "/requests",
        lead.get("/requests/new"),
        title="한글 답변 요청",
        requester="시범 요청자",
        due=due,
        team="collect",
        instructions="양식을 작성해 주세요.",
        upload=("기관 양식.hwpx", template),
    )
    assert (status, location) == (303, "/requests/2")
    request = lead.get(location)
    assert (
        lead.send(
            "POST",
            location + "/assign",
            request,
            version=field(request, "version"),
            department="planning",
            due=due,
        )[0]
        == 303
    )
    assert "기관 양식.hwpx" in planning.get("/assignments/3")
    assert planning.send("GET", "/requests/2/template")[1] == template
    for account in (operations, other):
        assert account.send("GET", "/requests/2/template")[0] == 404
    draft = planning.get("/assignments/3")
    assert (
        planning.send(
            "POST",
            "/assignments/3/submit",
            draft,
            version=field(draft, "version"),
            contact="담당자 02-000-0000",
            na="1",
            upload=("원본.hwp", submitted),
        )[0]
        == 400
    )
    assert (
        planning.send(
            "POST",
            "/assignments/3/submit",
            draft,
            version=field(draft, "version"),
            contact="담당자 02-000-0000",
            upload=("부서 답변.hwp", submitted),
        )[0]
        == 303
    )
    assert "첨부 문서로 제출" in planning.get("/assignments/3")
    review = reviewer.get("/assignments/3")
    assert (
        reviewer.send(
            "POST", "/assignments/3/approve", review, version=field(review, "version")
        )[0]
        == 303
    )
    draft = reviewer.get(location)
    assert (
        reviewer.send(
            "POST",
            location + "/prepare",
            draft,
            version=field(draft, "version"),
            collection=field(draft, "collection"),
            upload=("국회 최종.hwpx", original_final),
        )[0]
        == 303
    )
    assert reviewer.send("GET", location + "/document")[1] == original_final
    assert planning.send("GET", location + "/document")[0] == 404
    final = lead.get(location)
    assert (
        lead.send(
            "POST",
            location + "/finalize",
            final,
            version=field(final, "version"),
            collection=field(final, "collection"),
        )[0]
        == 303
    )
    assert lead.send("GET", "/finals/1/document")[1] == original_final
    assert reviewer.send("GET", "/finals/1/document")[0] == 404
    assert planning.send("GET", "/finals/1/document")[0] == 404
    assert lead.send("GET", location + "/document")[0] == 404
    status, package, _ = lead.send("GET", "/finals/1/download")
    assert status == 200
    with ZipFile(BytesIO(package)) as archive:
        assert archive.read("최종본/국회 최종.hwpx") == original_final
        assert archive.read("첨부/1-부서 답변.hwp") == submitted
        assert (
            sha256(original_final).hexdigest() in archive.read("취합근거.txt").decode()
        )
    request = lead.get(location)
    assert (
        lead.send(
            "POST",
            location + "/reopen",
            request,
            version=field(request, "version"),
            note="최종 한글 문서 수정",
        )[0]
        == 303
    )
    draft = lead.get(location)
    assert (
        lead.send(
            "POST",
            location + "/finalize",
            draft,
            version=field(draft, "version"),
            collection=field(draft, "collection"),
            upload=("두 번째 최종.hwp", submitted),
        )[0]
        == 303
    )
    assert lead.send("GET", "/finals/1/document")[1] == original_final
    assert lead.send("GET", "/finals/2/document")[1] == submitted


def test_version_one_migration_keeps_existing_requests_and_attachments(tmp_path):
    db = tmp_path / "existing.sqlite3"
    with store.connect(db, write=True) as conn:
        store.create_tables(conn, store.SCHEMA)
        conn.execute(f"PRAGMA application_id={store.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=1")
        seed_legacy_request(conn)
        conn.execute(
            "INSERT INTO attachments(assignment, name, sha256, content) VALUES (1, ?, ?, ?)",
            ("기존 답변.txt", sha256(b"old").hexdigest(), b"old"),
        )
    store.initialize(db)
    store.initialize(db)
    with store.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
        assert conn.execute("SELECT content FROM attachments").fetchone()[0] == b"old"
        assert conn.execute("SELECT count(*) FROM final_documents").fetchone()[0] == 0


def api_call(server, method, path, token="", payload=None, extra_headers=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    raw = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        raw = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )
    headers.update(extra_headers or {})
    conn = HTTPConnection(*server.server_address, timeout=5)
    try:
        conn.request(method, path, raw, headers)
        response = conn.getresponse()
        data = json.loads(response.read())
        return response.status, data
    finally:
        conn.close()


def test_partner_intake_replay_status_and_scope(pilot):
    db, users = pilot
    server = users["lead"].server
    path = integrations.REQUESTS
    payload = {
        "external_id": "ERP-105",
        "title": "외부 포털 접수",
        "requester": "협력 기관",
        "due": store.today(),
        "instructions": "관리팀이 요구 사항을 확인합니다.",
    }
    with store.connect(db, write=True) as conn:
        integrations.configure_client(conn, "client-a", "collect", True)
        integrations.configure_client(conn, "client-b", "other-team", True)
        integrations.configure_client(conn, "client-c", "collect", True)
        key_id, token, _ = integrations.issue_key(conn, "client-a", 5)
        _, other, _ = integrations.issue_key(conn, "client-b", 5)
        _, same_team, _ = integrations.issue_key(conn, "client-c", 5)
    assert api_call(server, "POST", path, payload=payload)[0] == 401
    assert (
        api_call(server, "POST", path, token, payload, {"Origin": server.origin})[0]
        == 400
    )
    assert (
        api_call(
            server, "POST", path, token, payload, {"Cookie": users["lead"].cookie}
        )[0]
        == 400
    )
    assert (
        api_call(server, "POST", path, token, dict(payload, team="other-team"))[0]
        == 400
    )
    assert (
        api_call(
            server, "POST", path, token, b'{"external_id":"one","external_id":"two"}'
        )[0]
        == 400
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: api_call(server, "POST", path, token, payload), range(2))
        )
    assert sorted(code for code, _ in results) == [200, 201]
    recorded = results[0][1]
    assert recorded == {
        "external_id": "ERP-105",
        "status": "received",
        "due": store.today(),
    }
    assert api_call(server, "POST", path, token, payload) == (200, recorded)
    assert (
        api_call(server, "POST", path, token, dict(payload, title="다른 내용"))[0]
        == 409
    )
    assert api_call(server, "GET", path + "/ERP-105", other)[0] == 404
    assert api_call(server, "GET", path + "/ERP-105", same_team)[0] == 404
    assert api_call(server, "GET", path + "/ERP-105", "not-a-key")[0] == 401
    assert api_call(server, "GET", path + "/ERP-105?download=true", token)[0] == 400
    assert users["planning"].send("GET", "/requests/2")[0] == 404
    assert users["other"].send("GET", "/requests/2")[0] == 404
    request = users["lead"].get("/requests/2")
    assert (
        users["lead"].send(
            "POST",
            "/requests/2/assign",
            request,
            version=field(request, "version"),
            department="planning",
            due=store.today(),
        )[0]
        == 303
    )
    assert (
        api_call(server, "GET", path + "/ERP-105", token)[1]["status"] == "collecting"
    )
    with store.connect(db, write=True) as conn:
        assert (
            conn.execute("SELECT count(*) FROM integration_requests").fetchone()[0] == 1
        )
        assert (
            conn.execute(
                "SELECT token_hash FROM integration_keys WHERE id=?", (key_id,)
            ).fetchone()[0]
            == sha256(token.encode()).hexdigest()
        )
        _, rotated, _ = integrations.issue_key(conn, "client-a", 10)
        integrations.revoke_key(conn, "client-a", key_id)
        integrations.configure_client(conn, "client-a", "collect", False)
    assert api_call(server, "GET", path + "/ERP-105", token)[0] == 401
    assert api_call(server, "GET", path + "/ERP-105", rotated)[0] == 200
    assert (
        api_call(server, "POST", path, rotated, dict(payload, external_id="ERP-106"))[0]
        == 403
    )
    with store.connect(db, write=True) as conn:
        conn.execute(
            "UPDATE integration_keys SET expires=? WHERE client='client-b'",
            (int(time()) - 1,),
        )
        integrations.disable_client(conn, "client-a")
    assert api_call(server, "GET", path + "/ERP-105", rotated)[0] == 401
    assert api_call(server, "POST", path, other, payload)[0] == 401


def test_dual_role_cannot_approve_own_submission(pilot):
    db, users = pilot
    with store.connect(db, write=True) as conn:
        set_grant(conn, "planning", "collect", "lead", True)
    planning = users["planning"]
    page = planning.get("/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            page,
            version=field(page, "version"),
            contact="담당자 02-000-0000",
            body="본인 제출",
        )[0]
        == 303
    )
    page = planning.get("/assignments/1")
    assert "다른 취합 담당자가 이 제출본을 승인할 수 있습니다." in page
    assert 'action="/assignments/1/approve"' not in page
    assert (
        planning.send(
            "POST", "/assignments/1/approve", page, version=field(page, "version")
        )[0]
        == 403
    )
    reviewer = users["reviewer"]
    page = reviewer.get("/assignments/1")
    assert (
        reviewer.send(
            "POST", "/assignments/1/approve", page, version=field(page, "version")
        )[0]
        == 303
    )


def test_portal_filters_and_retains_reply_when_attachment_is_rejected(pilot):
    _, users = pilot
    planning = users["planning"]
    assert "부서별 업무 추진 현황 제출" not in planning.get("/?status=done")
    assert "부서별 업무 추진 현황 제출" in planning.get("/?status=active")
    page = planning.get("/assignments/1")
    response, returned, _ = planning.send(
        "POST",
        "/assignments/1/submit",
        page,
        version=field(page, "version"),
        contact="담당자 02-000-0000",
        body="보존할 답변 <초안>",
        upload=("실패.pdf", b"not pdf"),
    )
    assert response == 400
    assert 'role="alert"' in returned
    assert "보존할 답변 &lt;초안&gt;" in returned
    assert field(returned, "version") == field(page, "version")
    assert "보존할 답변" not in planning.get("/assignments/1")


def test_search_scope_includes_only_the_actors_authorized_department_content(pilot):
    db, users = pilot
    planning, ops, reviewer = (
        users[name] for name in ("planning", "operations", "reviewer")
    )
    shared_title = "부서별 업무 추진 현황 제출"
    with store.connect(db, write=True) as conn:
        private_id = store.create_request(
            conn,
            "lead",
            {
                "team": "collect",
                "title": "운영팀 단독 요구자료",
                "requester": "단독 요청",
                "instructions": "재고확인전용",
                "due": store.today(),
            },
            [("재고양식.hwpx", sample_hwpx("운영 서식"))],
        )
        request, _, _ = store.request_for(conn, "lead", private_id)
        store.add_assignment(
            conn, "lead", request, {"department": "operations", "due": store.today()}
        )
        assert json.loads(operations.account_access(conn, "planning"))[
            "effective_grants"
        ] == [{"scope": "planning", "role": "department", "name": "기획부서"}]

    def search(client, term):
        return client.get("/?" + urlencode({"q": term}))

    assert "운영팀 단독 요구자료" in search(ops, "재고확인전용")
    assert "운영팀 단독 요구자료" not in search(planning, "재고확인전용")
    assert "운영팀 단독 요구자료" not in search(planning, "재고양식")
    assert planning.send("GET", f"/requests/{private_id}")[0] == 404
    assert planning.send("GET", f"/requests/{private_id}/template")[0] == 404
    assert ops.send("GET", f"/requests/{private_id}/template")[0] == 200

    draft = planning.get("/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/draft",
            draft,
            version=field(draft, "version"),
            body="기획초안전용",
            upload=("기획비공개.txt", b"private"),
        )[0]
        == 303
    )
    submission = ops.get("/assignments/2")
    assert (
        ops.send(
            "POST",
            "/assignments/2/submit",
            submission,
            version=field(submission, "version"),
            contact="담당자 02-000-0000",
            body="운영제출문구",
            upload=("운영증빙자료.txt", b"submitted"),
        )[0]
        == 303
    )

    for term in ("기획초안전용", "기획비공개"):
        assert shared_title in search(planning, term)
        assert shared_title not in search(ops, term)
        assert shared_title not in search(reviewer, term)
    for term in ("운영제출문구", "운영증빙자료"):
        assert shared_title in search(ops, term)
        assert shared_title in search(reviewer, term)
        assert shared_title not in search(planning, term)
    shared = planning.get("/requests/1")
    assert "운영부서" not in shared
    assert planning.send("GET", "/assignments/2")[0] == 404
    assert planning.send("GET", "/attachments/2")[0] == 404

    with store.connect(db, write=True) as conn:
        set_grant(conn, "planning", "planning", "department", False)
        assert (
            json.loads(operations.account_access(conn, "planning"))["effective_grants"]
            == []
        )
    assert shared_title not in search(planning, "기획초안전용")
    assert planning.send("GET", "/requests/1")[0] == 404
    assert planning.send("GET", "/attachments/1")[0] == 404


def test_verified_backup_and_audit(pilot, tmp_path):
    db, users = pilot
    with store.connect(db, write=True) as conn:
        integrations.configure_client(conn, "recovery", "collect", True)
        _, token, _ = integrations.issue_key(conn, "recovery", 5)
    target = tmp_path / "verified-copy.sqlite3"
    operations.snapshot(db, target)
    with operations.open_portal(target) as conn:
        assert operations.verify(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
        log = [json.loads(line) for line in operations.audit_lines(conn, 0, 1000)]
        assert any(row["action"] == "권한 부여" for row in log)
    with pytest.raises(store.PortalError):
        operations.snapshot(db, target)
    with store.connect(target, write=True) as conn:
        operations.invalidate_access(conn)
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        with pytest.raises(store.PortalError):
            integrations.authenticate(conn, "Bearer " + token)
    assert (
        api_call(users["lead"].server, "GET", integrations.REQUESTS + "/absent", token)[
            0
        ]
        == 404
    )


def test_https_proxy_headers_and_integration_default(tmp_path):
    path = tmp_path / "private.sqlite3"
    store.initialize(path)
    with PortalServer(
        ("127.0.0.1", 0), path, "https://collection.example.org"
    ) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            assert (
                api_call(
                    server,
                    "GET",
                    integrations.REQUESTS + "/test",
                    extra_headers={"Host": "collection.example.org"},
                )[0]
                == 404
            )
            conn = HTTPConnection(*server.server_address, timeout=5)
            try:
                conn.request("GET", "/", headers={"Host": "collection.example.org"})
                response = conn.getresponse()
                cookie = response.getheader("Set-Cookie")
                assert response.status == 200
                assert response.getheader("Strict-Transport-Security")
                assert (
                    cookie.startswith("__Host-collection_session=")
                    and "; Secure" in cookie
                )
                response.read()
                conn.request("GET", "/", headers={"Host": "unapproved.example.org"})
                response = conn.getresponse()
                assert response.status == 403
                response.read()
            finally:
                conn.close()
        finally:
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("version", [2, 3])
def test_portal_migrates_without_losing_hancom_data_or_publishing_old_activity(
    tmp_path, version
):
    path = tmp_path / "v2.sqlite3"
    template = sample_hwpx("보존 대상")
    with store.connect(path, write=True) as conn:
        store.create_tables(conn, store.SCHEMA)
        store.create_tables(conn, store.DOCUMENT_SCHEMA)
        if version == 3:
            store.create_tables(conn, store.INTEGRATION_SCHEMA)
        conn.execute(f"PRAGMA application_id={store.APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        seed_legacy_request(conn)
        store.event(conn, "former", "부서 배정", 1, 1)
        conn.execute(
            "INSERT INTO request_documents VALUES (1, ?, ?, ?)",
            ("양식.hwpx", sha256(template).hexdigest(), template),
        )
    store.initialize(path)
    with operations.open_portal(path) as conn:
        assert operations.verify(conn) == store.SCHEMA_VERSION
        assert (
            conn.execute(
                "SELECT content FROM request_documents WHERE request_id=1"
            ).fetchone()[0]
            == template
        )
        assert conn.execute("SELECT count(*) FROM notices").fetchone()[0] == 0


def seed_legacy_request(conn):
    conn.executemany(
        "INSERT INTO scopes VALUES (?, ?, ?)",
        (("collect", "취합팀", "team"), ("planning", "기획부서", "department")),
    )
    conn.execute(
        "INSERT INTO requests(id, team, title, requester, instructions, due) "
        "VALUES (1, 'collect', '기존 요구', '요구자', '내용', ?)",
        (store.today(),),
    )
    conn.execute(
        "INSERT INTO assignments(id, request_id, department, due) "
        "VALUES (1, 1, 'planning', ?)",
        (store.today(),),
    )


def test_workflow_notices_open_authorized_task_and_keep_separate_read_receipts(pilot):
    db, users = pilot
    planning, reviewer, lead = (
        users[role] for role in ("planning", "reviewer", "lead")
    )
    page = planning.get("/notices?status=unread")
    assigned = re.search(r'action="(/notices/\d+/open)"', page)[1]
    assert "부서 배정" in page and "운영부서" not in page
    assert planning.send("POST", assigned, page)[2] == "/assignments/1"
    assert assigned not in planning.get("/notices?status=unread")
    draft = planning.get("/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/draft",
            draft,
            version=field(draft, "version"),
            body="초안 문구",
        )[0]
        == 303
    )
    assert "초안 저장" not in reviewer.get("/notices")
    draft = planning.get("/assignments/1")
    assert (
        planning.send(
            "POST",
            "/assignments/1/submit",
            draft,
            version=field(draft, "version"),
            contact="담당자 02-000-0000",
            body="제출 문구",
        )[0]
        == 303
    )
    page = reviewer.get("/notices?" + urlencode({"status": "unread", "q": "부서 제출"}))
    submission = re.search(r'action="(/notices/\d+/open)"', page)[1]
    assert reviewer.send("POST", submission, page)[2] == "/assignments/1"
    assert submission not in reviewer.get("/notices?status=unread")
    assert submission in lead.get("/notices?status=unread")
    review = reviewer.get("/assignments/1")
    assert (
        reviewer.send(
            "POST",
            "/assignments/1/return",
            review,
            version=field(review, "version"),
            note="증빙 확인 요청",
            due=store.today(),
        )[0]
        == 303
    )
    assert "증빙 확인 요청" in planning.get("/notices?status=unread")
    assert "증빙 확인 요청" not in users["operations"].get("/notices?status=unread")
    with store.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM notice_reads").fetchone()[0] == 2


def test_manual_announcement_search_and_read_recheck_current_recipient_scope(pilot):
    db, users = pilot
    lead, planning, ops, other = (
        users[key] for key in ("lead", "planning", "operations", "other")
    )
    path = "/requests/1/announce"
    token = "기획부전용고유공지"
    assert (
        planning.send(
            "POST", path, planning.get("/requests/1"), department="1", notice_body=token
        )[0]
        == 404
    )
    assert (
        lead.send(
            "POST", path, lead.get("/requests/1"), department="999", notice_body=token
        )[0]
        == 404
    )
    assert (
        lead.send(
            "POST", path, lead.get("/requests/1"), department="1", notice_body=token
        )[0]
        == 303
    )
    search = "?" + urlencode({"q": token})
    assert "부서별 업무 추진 현황 제출" in planning.get("/" + search)
    assert "부서별 업무 추진 현황 제출" not in ops.get("/" + search)
    assert "부서별 업무 추진 현황 제출" not in other.get("/" + search)
    assert token in planning.get("/requests/1")
    assert token not in ops.get("/requests/1")
    page = planning.get("/notices" + search)
    action = re.search(r'action="(/notices/\d+/open)"', page)[1]
    assert ops.send("POST", action, ops.get("/notices"))[0] == 404
    assert "#notice-" in planning.send("POST", action, page)[2]
    assert planning.send("POST", action, page)[0] == 303
    broadcast = "기존 배정 부서 안내"
    assert (
        lead.send(
            "POST",
            path,
            lead.get("/requests/1"),
            department="all",
            notice_body=broadcast,
        )[0]
        == 303
    )
    with store.connect(db, write=True) as conn:
        assert conn.execute("SELECT count(*) FROM notice_reads").fetchone()[0] == 1
        conn.execute("INSERT INTO scopes VALUES ('later', '나중 부서', 'department')")
        set_grant(conn, "other", "later", "department", True)
        request, _, _ = store.request_for(conn, "lead", 1)
        store.add_assignment(
            conn, "lead", request, {"department": "later", "due": store.today()}
        )
        set_grant(conn, "planning", "planning", "department", False)
    assert token not in planning.get("/notices")
    assert planning.send("POST", action, page)[0] == 404
    assert broadcast in ops.get("/requests/1")
    assert broadcast not in other.get("/requests/1")
    assert broadcast not in other.get("/notices")
