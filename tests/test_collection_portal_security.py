"""Production authentication controls of the collection portal (schema 5).

Covers persistent lockout, idle sessions, TOTP, required MFA, OIDC sign-in, the audit hash chain
and the upload scanner hook. All servers run on loopback against temporary databases.
"""

import base64
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from time import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from scripts.collection_portal import auth, oidc, operations, scanning, store
from scripts.collection_portal.__main__ import demo, validate_password
from scripts.collection_portal.http import PortalServer
from tests.test_collection_portal import Client, field


class Browser(Client):
    """A client that has not signed in yet."""

    def __init__(self, server):  # noqa: D107 - deliberately skips Client's automatic login
        self.server = server
        self.cookie = ""

    def login(self, login, password, code=""):
        _, page, _ = self.send("GET", "/")
        status, body, _ = self.send(
            "POST", "/login", page, login=login, password=password, code=code
        )
        return status, body


@contextmanager
def serve(db, **options):
    with PortalServer(("127.0.0.1", 0), db, **options) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join()


@pytest.fixture
def portal_db(tmp_path):
    db = tmp_path / "portal.sqlite3"
    store.initialize(db)
    with store.connect(db, write=True) as conn:
        users = dict(demo(conn))
    return db, users


def actions(db):
    with store.connect(db) as conn:
        return [
            row["action"]
            for row in conn.execute("SELECT action FROM events ORDER BY id")
        ]


# --- lockout ----------------------------------------------------------------------------------


def test_lockout_persists_across_restart_and_console_unlock(portal_db):
    db, users = portal_db
    with serve(db) as server:
        browser = Browser(server)
        for _ in range(auth.MAX_FAILURES):
            assert browser.login("planning", "wrong-password-123")[0] == 401
        status, page = browser.login("planning", users["planning"])
        assert status == 401 and auth.FAILED in page  # same text as a wrong password
    with serve(db) as server:  # a restart must not clear the lock
        assert Browser(server).login("planning", users["planning"])[0] == 401
        with store.connect(db, write=True) as conn:
            auth.unlock(conn, "planning")
        assert Browser(server).login("planning", users["planning"])[0] == 303
    recorded = actions(db)
    assert (
        "계정 잠금" in recorded
        and "로그인 거부(잠금 중)" in recorded
        and "계정 잠금 해제" in recorded
    )
    assert recorded.count("로그인 실패") == auth.MAX_FAILURES - 1


def test_unknown_account_failures_are_not_recorded(portal_db):
    db, _ = portal_db
    before = len(actions(db))
    with serve(db) as server:
        assert Browser(server).login("secret-typed-into-login", "x" * 12)[0] == 401
    assert len(actions(db)) == before


# --- sessions ---------------------------------------------------------------------------------


def test_idle_session_expires_and_activity_is_recorded(portal_db):
    db, users = portal_db
    with serve(db, idle_seconds=600) as server:
        client = Client(server, "planning", users["planning"])
        with store.connect(db, write=True) as conn:
            conn.execute(
                "UPDATE sessions SET last_seen=? WHERE login='planning'",
                (int(time()) - 120,),
            )
        assert "로그아웃" in client.get("/")
        with store.connect(db) as conn:
            seen = conn.execute(
                "SELECT last_seen FROM sessions WHERE login='planning'"
            ).fetchone()[0]
        assert seen >= int(time()) - 5
        with store.connect(db, write=True) as conn:
            conn.execute(
                "UPDATE sessions SET last_seen=? WHERE login='planning'",
                (int(time()) - 601,),
            )
        page = client.get("/")
        assert 'name="password"' in page and "로그아웃" not in page
        draft = Client(server, "planning", users["planning"]).get("/assignments/1")
        assert (
            client.send(
                "POST",
                "/assignments/1/draft",
                draft,
                version=field(draft, "version"),
                body="유휴 세션",
            )[0]
            == 401
        )


def test_console_logout_and_session_listing(portal_db):
    db, users = portal_db
    with serve(db) as server:
        client = Client(server, "planning", users["planning"])
        with store.connect(db) as conn:
            rows = auth.list_sessions(conn, "planning")
        assert len(rows) == 1 and rows[0]["method"] == "password"
        with store.connect(db, write=True) as conn:
            assert auth.end_sessions(conn, "planning") == 1
        assert "로그아웃" not in client.get("/")


def test_password_policy_rejects_weak_choices():
    assert validate_password("staff01", "short")
    assert validate_password("staff01", "my-staff01-password")
    assert validate_password("staff01", "aaaaaaaaaaaaaaaa")
    assert validate_password("staff01", "correct horse battery") == ""


# --- TOTP -------------------------------------------------------------------------------------


def test_totp_matches_rfc6238_reference_vector():
    # RFC 6238 Appendix B, SHA-1 secret "12345678901234567890", T=59 → 94287082 (8 digits).
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"


def test_totp_login_rejects_missing_wrong_and_replayed_codes(portal_db):
    db, users = portal_db
    secret = auth.new_totp_secret()
    step = int(time() // auth.TOTP_PERIOD)
    with store.connect(db, write=True) as conn:
        auth.enroll_totp(conn, "lead", secret, auth.totp_code(secret, step - 1))
    with serve(db) as server:
        browser = Browser(server)
        assert browser.login("lead", users["lead"])[0] == 401
        valid = {auth.totp_code(secret, s) for s in range(step - 2, step + 3)}
        wrong = next(
            code
            for code in ("000000", "111111", "222222", "333333")
            if code not in valid
        )
        assert browser.login("lead", users["lead"], wrong)[0] == 401
        assert (
            browser.login("lead", users["lead"], auth.totp_code(secret, step - 1))[0]
            == 401
        )  # enrolment step
        assert (
            Browser(server).login("lead", users["lead"], auth.totp_code(secret, step))[
                0
            ]
            == 303
        )
        assert (
            Browser(server).login("lead", users["lead"], auth.totp_code(secret, step))[
                0
            ]
            == 401
        )  # replay
    with store.connect(db, write=True) as conn:
        auth.reset_totp(conn, "lead")
        assert (
            conn.execute(
                "SELECT totp_secret FROM accounts WHERE login='lead'"
            ).fetchone()[0]
            is None
        )


def test_require_mfa_blocks_unenrolled_password_login(portal_db):
    db, users = portal_db
    with serve(db, require_mfa=True) as server:
        status, page = Browser(server).login("planning", users["planning"])
        assert status == 401 and auth.MFA_REQUIRED in page
        status, page = Browser(server).login("planning", "wrong-password-123")
        assert auth.MFA_REQUIRED not in page  # only shown after the password is proven


# --- audit chain ------------------------------------------------------------------------------


def test_audit_chain_detects_changed_and_deleted_events(portal_db, tmp_path):
    db, _ = portal_db
    with operations.open_portal(db) as conn:
        assert operations.verify(conn) == store.SCHEMA_VERSION
        head = json.loads(operations.audit_head(conn))
        assert head["last_event_id"] > 3 and len(head["chain"]) == 64
        assert all(
            "chain" in json.loads(line) for line in operations.audit_lines(conn, 0, 10)
        )
    for tamper in (
        "UPDATE events SET detail='변조' WHERE id=2",
        "DELETE FROM events WHERE id=2",
    ):
        copy = tmp_path / f"copy-{len(tamper)}.sqlite3"
        operations.snapshot(db, copy)
        with sqlite3.connect(copy) as conn:
            assert conn.execute(tamper).rowcount == 1
        with operations.open_portal(copy) as conn:
            with pytest.raises(store.PortalError, match="감사 이력"):
                operations.verify(conn)


def test_schema_four_migration_chains_history_and_ends_sessions(tmp_path):
    path = tmp_path / "v4.sqlite3"
    with store.connect(path, write=True) as conn:
        for schema in (
            store.SCHEMA,
            store.DOCUMENT_SCHEMA,
            store.INTEGRATION_SCHEMA,
            store.NOTICE_SCHEMA,
        ):
            store.create_tables(conn, schema)
        conn.execute(f"PRAGMA application_id={store.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=4")
        conn.execute(
            "INSERT INTO accounts(login, password) VALUES ('old', ?)",
            (store.password_hash("x" * 12),),
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('h', 'old', ?)", (int(time()) + 3600,)
        )
        store.event(conn, "former", "예전 기록")
        store.event(conn, "former", "예전 기록 2")
    store.initialize(path)
    with operations.open_portal(path) as conn:
        assert operations.verify(conn) == store.SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert [
            r[0] for r in conn.execute("SELECT action FROM events ORDER BY id")
        ] == ["예전 기록", "예전 기록 2"]
    with store.connect(path, write=True) as conn:
        store.event(conn, "new", "새 기록")
    with operations.open_portal(path) as conn:
        assert json.loads(operations.audit_head(conn))["last_event_id"] == 3


# --- upload scanning --------------------------------------------------------------------------

SCANNER = [
    sys.executable,
    "-I",
    "-c",
    "import sys; sys.exit(7 if b'EICAR' in open(sys.argv[1], 'rb').read() else 0)",
    "{path}",
]


def test_scanner_blocks_flagged_upload_and_fails_closed(portal_db):
    db, users = portal_db
    with serve(db, scanner=scanning.Scanner(SCANNER)) as server:
        planning = Client(server, "planning", users["planning"])
        draft = planning.get("/assignments/1")
        version = field(draft, "version")
        status, page, _ = planning.send(
            "POST",
            "/assignments/1/draft",
            draft,
            version=version,
            body="첨부",
            upload=("위험.txt", b"X5O!P%@AP EICAR test"),
        )
        assert status == 422 and scanning.BLOCKED in page
        assert (
            planning.send(
                "POST",
                "/assignments/1/draft",
                draft,
                version=version,
                body="첨부",
                upload=("정상.txt", "정상 파일".encode()),
            )[0]
            == 303
        )
    missing = scanning.Scanner([str(db.parent / "no-such-scanner.exe"), "{path}"])
    with serve(db, scanner=missing) as server:
        planning = Client(server, "planning", users["planning"])
        draft = planning.get("/assignments/1")
        assert (
            planning.send(
                "POST",
                "/assignments/1/draft",
                draft,
                version=field(draft, "version"),
                body="x",
                upload=("정상2.txt", b"ok"),
            )[0]
            == 503
        )
    with store.connect(db) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM attachments")]
    assert names == ["정상.txt"]


def test_scan_command_validation():
    assert scanning.parse_command('["scan.exe", "-f", "{path}"]')[-1] == "{path}"
    for bad in (
        '"scan.exe {path}"',
        '["scan.exe"]',
        '["a", "{path}", "{path}"]',
        "not json",
    ):
        with pytest.raises(ValueError):
            scanning.parse_command(bad)


# --- OIDC -------------------------------------------------------------------------------------

crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: E402

CLIENT_ID = "collection-portal"


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.fixture(scope="module")
def signing_key():
    return crypto.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key, kid="k1"):
    numbers = key.public_key().public_numbers()
    size = (numbers.n.bit_length() + 7) // 8
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64(numbers.n.to_bytes(size, "big")),
        "e": b64(numbers.e.to_bytes(3, "big")),
    }


def sign(key, claims, header=None):
    header = header or {"alg": "RS256", "kid": "k1", "typ": "JWT"}
    signing_input = (
        b64(json.dumps(header).encode()) + "." + b64(json.dumps(claims).encode())
    )
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + b64(signature)


class FakeIdp(ThreadingHTTPServer):
    def __init__(self, key):
        self.key = key
        self.next_token = ""
        self.token_requests = []
        super().__init__(("127.0.0.1", 0), IdpHandler)
        self.issuer = f"http://127.0.0.1:{self.server_port}"


class IdpHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        idp = self.server
        if self.path == "/.well-known/openid-configuration":
            return self.reply(
                {
                    "issuer": idp.issuer,
                    "authorization_endpoint": idp.issuer + "/authorize",
                    "token_endpoint": idp.issuer + "/token",
                    "jwks_uri": idp.issuer + "/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"],
                }
            )
        if self.path == "/jwks":
            return self.reply({"keys": [jwk(idp.key)]})
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.token_requests.append(
            (dict(self.headers), parse_qs(self.rfile.read(length).decode()))
        )
        self.reply(
            {
                "id_token": self.server.next_token,
                "token_type": "Bearer",
                "access_token": "unused",
            }
        )


@pytest.fixture
def idp(signing_key):
    server = FakeIdp(signing_key)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join()


def provider_for(idp, **extra):
    config = {
        "issuer": idp.issuer,
        "client_id": CLIENT_ID,
        "client_secret_env": "PORTAL_TEST_SECRET",
        **extra,
    }
    return oidc.OidcProvider.from_config(
        config, {"PORTAL_TEST_SECRET": "s3cret:/value"}
    )


def claims_for(idp, nonce, **changes):
    issued = int(time())
    claims = {
        "iss": idp.issuer,
        "aud": CLIENT_ID,
        "sub": "emp-001",
        "iat": issued,
        "exp": issued + 300,
        "nonce": nonce,
        "email": "planning@example.org",
    }
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not None}


def request(server, path, cookie=""):
    conn = HTTPConnection(*server.server_address, timeout=10)
    try:
        conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
        response = conn.getresponse()
        cookies = [
            value for key, value in response.getheaders() if key.lower() == "set-cookie"
        ]
        return (
            response.status,
            response.getheader("Location"),
            cookies,
            response.read().decode("utf-8", "replace"),
        )
    finally:
        conn.close()


def start(server):
    status, location, cookies, _ = request(server, "/sso/start")
    assert status == 303
    query = parse_qs(urlsplit(location).query)
    binding = next(
        c.split(";", 1)[0] for c in cookies if c.startswith("collection_sso=")
    )
    assert "SameSite=Lax" in cookies[0]
    return query, binding


def test_oidc_sign_in_maps_only_linked_identity(portal_db, idp, signing_key):
    db, _ = portal_db
    with serve(db, oidc_provider=provider_for(idp)) as server:
        query, binding = start(server)
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [server.origin + "/sso/callback"]
        idp.next_token = sign(signing_key, claims_for(idp, query["nonce"][0]))
        callback = "/sso/callback?" + urlencode(
            {"code": "c1", "state": query["state"][0]}
        )
        assert (
            request(server, callback, binding)[0] == 403
        )  # not linked yet; e-mail claim is ignored
        assert "SSO 로그인 거부(미연결 계정)" in actions(db)

        with store.connect(db, write=True) as conn:
            oidc.link_identity(conn, store, "planning", idp.issuer, "emp-001", "test")
        query, binding = start(server)
        idp.next_token = sign(signing_key, claims_for(idp, query["nonce"][0]))
        callback = "/sso/callback?" + urlencode(
            {"code": "c2", "state": query["state"][0]}
        )
        status, _, cookies, page = request(server, callback, binding)
        assert status == 200 and 'http-equiv="refresh"' in page
        session = next(
            c.split(";", 1)[0] for c in cookies if c.startswith("collection_session=")
        )
        assert "SameSite=Strict" in next(
            c for c in cookies if c.startswith("collection_session=")
        )
        assert "로그아웃" in request(server, "/", session)[3]
        headers, form = idp.token_requests[-1]
        verifier = form["code_verifier"][0]
        assert (
            b64(hashlib.sha256(verifier.encode()).digest())
            == query["code_challenge"][0]
        )
        assert headers["Authorization"].startswith("Basic ") and form["code"] == ["c2"]
        assert request(server, callback, binding)[0] == 400  # state is single use

        query, _ = start(server)
        idp.next_token = sign(signing_key, claims_for(idp, query["nonce"][0]))
        callback = "/sso/callback?" + urlencode(
            {"code": "c3", "state": query["state"][0]}
        )
        assert (
            request(server, callback, "collection_sso=" + "A" * 43)[0] == 400
        )  # other browser

        with store.connect(db, write=True) as conn:
            conn.execute("UPDATE accounts SET enabled=0 WHERE login='planning'")
        query, binding = start(server)
        idp.next_token = sign(signing_key, claims_for(idp, query["nonce"][0]))
        callback = "/sso/callback?" + urlencode(
            {"code": "c4", "state": query["state"][0]}
        )
        assert request(server, callback, binding)[0] == 403
    with store.connect(db) as conn:
        methods = {row[0] for row in conn.execute("SELECT method FROM sessions")}
    assert methods == {"sso"}


@pytest.mark.parametrize(
    "change",
    [
        {"nonce": "different"},
        {"aud": "other-client"},
        {"aud": [CLIENT_ID, "x"]},
        {"exp": 1},
        {"iat": 4_000_000_000},
        {"iss": "https://evil.example"},
        {"sub": None},
    ],
)
def test_oidc_rejects_untrusted_claims(idp, signing_key, change):
    provider = provider_for(idp)
    claims = claims_for(idp, "n-1")
    claims.update(change)
    token = sign(signing_key, {k: v for k, v in claims.items() if v is not None})
    with pytest.raises(oidc.OidcError):
        provider.verify_id_token(token, "n-1")


def test_oidc_rejects_forged_signatures_and_algorithms(idp, signing_key):
    provider = provider_for(idp)
    good = sign(signing_key, claims_for(idp, "n-1"))
    assert provider.verify_id_token(good, "n-1")["sub"] == "emp-001"
    head, body, signature = good.split(".")
    forged_body = b64(json.dumps(claims_for(idp, "n-1", sub="admin")).encode())
    other = crypto.generate_private_key(public_exponent=65537, key_size=2048)
    for token in (
        f"{head}.{forged_body}.{signature}",
        sign(other, claims_for(idp, "n-1")),
        b64(json.dumps({"alg": "none"}).encode()) + "." + body + ".",
        sign(
            signing_key,
            claims_for(idp, "n-1"),
            {"alg": "RS256", "kid": "k1", "crit": ["x"]},
        ),
        sign(signing_key, claims_for(idp, "n-1"), {"alg": "RS256", "kid": "unknown"}),
    ):
        with pytest.raises(oidc.OidcError):
            provider.verify_id_token(token, "n-1")


def test_oidc_required_amr_and_config_validation(idp, signing_key):
    provider = provider_for(idp, required_amr=["mfa", "otp"])
    with pytest.raises(oidc.OidcError):
        provider.verify_id_token(
            sign(signing_key, claims_for(idp, "n", amr=["pwd"])), "n"
        )
    assert provider.verify_id_token(
        sign(signing_key, claims_for(idp, "n", amr=["pwd", "mfa"])), "n"
    )
    with pytest.raises(oidc.OidcError):
        oidc.OidcProvider.from_config(
            {
                "issuer": "http://idp.example",
                "client_id": "a",
                "client_secret_env": "S",
            },
            {"S": "x"},
        )
    with pytest.raises(oidc.OidcError):
        oidc.OidcProvider.from_config(
            {"issuer": idp.issuer, "client_id": "a", "client_secret_env": "S"}, {}
        )
    with pytest.raises(oidc.OidcError):
        provider_for(idp, unexpected=True)


def test_sso_only_disables_password_login(portal_db, idp):
    db, users = portal_db
    with serve(db, oidc_provider=provider_for(idp), password_login=False) as server:
        browser = Browser(server)
        page = browser.get("/")
        assert "/sso/start" in page and 'name="password"' not in page
        assert (
            browser.send(
                "POST", "/login", login="planning", password=users["planning"]
            )[0]
            == 404
        )
    with pytest.raises(ValueError):
        PortalServer(("127.0.0.1", 0), db, password_login=False)


def test_identity_cannot_be_linked_to_two_accounts(portal_db, idp):
    db, _ = portal_db
    with store.connect(db, write=True) as conn:
        oidc.link_identity(conn, store, "planning", idp.issuer, "emp-9", "test")
        with pytest.raises(store.PortalError):
            oidc.link_identity(conn, store, "operations", idp.issuer, "emp-9", "test")
        oidc.unlink_identity(conn, store, idp.issuer, "emp-9", "test")
        oidc.link_identity(conn, store, "operations", idp.issuer, "emp-9", "test")
