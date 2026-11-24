"""Loopback HTTP entry point for the authenticated portal."""

import hmac
import json
import logging
import re
import secrets
import sqlite3
from email import policy
from email.parser import BytesParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from tempfile import SpooledTemporaryFile
from threading import BoundedSemaphore, Lock
from time import monotonic, time
from typing import Union, cast
from urllib.parse import parse_qs, quote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from . import (
    auth,
    documents,
    gongmun,
    integrations,
    notices,
    oidc,
    pages,
    questions,
    store,
)

COOKIE = "collection_session"
SSO_COOKIE = "collection_sso"
SESSION_SECONDS = auth.SESSION_SECONDS
SSO_START = "/sso/start"
SSO_CALLBACK = "/sso/callback"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
ITEM = re.compile(
    r"/(requests|assignments|attachments|finals|notices)/([1-9][0-9]{0,17})(?:/([a-z]+))?"
)
MAX_BODY = documents.MAX_SUBMISSION_FILES_SIZE + 7 * documents.MIB
# 질의별 답변(최대 200)·부서 체크 상자를 담을 수 있게 한다.
MAX_FORM_FIELDS = questions.MAX_QUESTIONS + 60
MULTIPART_FIELDS = {
    "csrf",
    "version",
    "body",
    "na",
    "team",
    "title",
    "requester",
    "due",
    "ledger_id",
    "instructions",
    "collection",
    "exception",
    "clear_document",
    "doc_no",
    "received_on",
    "committee",
    "basis",
    "privacy_ack",
    "contact",
    "base_date",
    "disclosure",
    "ground",
    "disclosure_reason",
    "personal_info",
}
ATTACHMENT_SQL = (
    "SELECT a.id, a.name, a.sha256, length(a.content) AS size, "
    "s.revision, d.name AS department FROM final_sources f JOIN submissions s ON s.id=f.submission "
    "JOIN assignments p ON p.id=s.assignment JOIN scopes d ON d.id=p.department "
    "JOIN attachments a ON a.submission=s.id WHERE f.final=? ORDER BY a.id"
)
Body = Union[str, bytes]
Response = Union[
    tuple[int, Body], tuple[int, Body, str], tuple[int, Body, str, dict[str, str]]
]


class PortalServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        db,
        origin=None,
        *,
        enable_integrations=False,
        idle_seconds=auth.IDLE_SECONDS,
        require_mfa=False,
        oidc_provider=None,
        password_login=True,
        scanner=None,
    ):
        if not password_login and oidc_provider is None:
            raise ValueError("비밀번호 로그인을 끄려면 OIDC 설정이 필요합니다.")
        self.db = db
        self.secret = secrets.token_bytes(32)
        self.login_attempts = []
        self.integration_attempts = {}
        self.enable_integrations = enable_integrations
        self.idle_seconds = idle_seconds
        self.require_mfa = require_mfa
        self.oidc = oidc_provider
        self.password_login = password_login
        self.scanner = scanner
        self.request_slots = BoundedSemaphore(24)
        self.attempt_lock = Lock()
        super().__init__(address, Handler)
        self.origin = origin or f"http://127.0.0.1:{self.server_port}"
        self.host = urlsplit(self.origin).netloc
        self.secure_cookie = self.origin.startswith("https://")

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            client_socket = cast(socket, request)
            try:
                client_socket.settimeout(1)
                client_socket.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\nRetry-After: 5\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def throttle_login(self, login):
        timestamp = monotonic()
        login = login.strip().casefold()[:80]
        with self.attempt_lock:
            self.login_attempts = [
                entry for entry in self.login_attempts if timestamp - entry[0] < 60
            ]
            if (
                len(self.login_attempts) >= 120
                or sum(name == login for _, name in self.login_attempts) >= 12
            ):
                raise store.PortalError(
                    429, "로그인 시도가 많습니다. 잠시 뒤 다시 시도해 주세요."
                )
            self.login_attempts.append((timestamp, login))

    def throttle_integration(self, client):
        window = int(monotonic()) // 60
        with self.attempt_lock:
            self.integration_attempts = {
                key: value
                for key, value in self.integration_attempts.items()
                if value[0] == window
            }
            _, count = self.integration_attempts.get(client, (window, 0))
            if count >= 120:
                raise store.PortalError(
                    429, "연동 호출 한도를 초과했습니다. 잠시 뒤 다시 시도해 주세요."
                )
            self.integration_attempts[client] = (window, count + 1)


class Handler(BaseHTTPRequestHandler):
    server_version = "CollectionPortal"
    sys_version = ""

    @property
    def portal(self) -> PortalServer:
        return cast(PortalServer, self.server)

    def setup(self):
        self.request_id = secrets.token_hex(12)
        self.verified_actor = None
        self.session_stale = False
        self.body_read_started = False
        self.request.settimeout(20)
        super().setup()

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def log_request(self, code="-", size="-"):
        path = getattr(self, "path", "")
        surface = "integration" if path.startswith("/integrations/") else "portal"
        operation = "login" if path == "/login" else self.command
        if operation not in ("login", "GET", "POST"):
            operation = "other"
        logging.info(
            "요청 id=%s surface=%s operation=%s status=%s actor=%r",
            self.request_id,
            surface,
            operation,
            code,
            self.verified_actor,
        )

    def log_error(self, format, *args):
        # BaseHTTPRequestHandler may include an invalid raw request line in its error message.
        logging.warning("HTTP 프로토콜 오류 id=%s", self.request_id)

    def respond(
        self, code, body: Body = b"", kind="text/html; charset=utf-8", headers=None
    ):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("X-Request-ID", self.request_id)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Native same-origin POST forms retain their Origin under this policy.
        self.send_header(
            "Referrer-Policy",
            "same-origin" if kind.startswith("text/html") else "no-referrer",
        )
        if self.portal.secure_cookie:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        for key, value in (headers or {}).items():
            for item in value if isinstance(value, list) else [value]:
                self.send_header(key, item)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def cookie(self, token, age=SESSION_SECONDS):
        secure = "; Secure" if self.portal.secure_cookie else ""
        return f"{self.cookie_name()}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"

    def cookie_name(self, base=COOKIE):
        return "__Host-" + base if self.portal.secure_cookie else base

    def sso_cookie(self, token, age):
        # Lax: the IdP returns the browser with a cross-site top-level GET, which drops Strict cookies.
        secure = "; Secure" if self.portal.secure_cookie else ""
        return f"{self.cookie_name(SSO_COOKIE)}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"

    def get_token(self, base=COOKIE):
        try:
            cookie = SimpleCookie(self.header("Cookie")).get(self.cookie_name(base))
        except CookieError:
            return ""
        return cookie.value if cookie and TOKEN.fullmatch(cookie.value) else ""

    def login_page(self, token, message=""):
        provider = self.portal.oidc
        return pages.login(
            self.csrf(token),
            message,
            password=self.portal.password_login,
            sso_label=provider.label if provider else None,
        )

    def csrf(self, token):
        return hmac.digest(self.portal.secret, token.encode(), "sha256").hex()

    def header(self, name):
        values = self.headers.get_all(name, [])
        if len(values) > 1:
            raise store.PortalError(400, "요청 헤더가 중복되었습니다.")
        return values[0] if values else ""

    def json_response(self, status, data, headers=None):
        self.respond(
            status,
            json.dumps(data, ensure_ascii=False),
            "application/json; charset=utf-8",
            headers,
        )

    def dispatch(self):
        api = self.path.startswith("/integrations/")
        retry = None
        try:
            if self.header("Host") != self.portal.host:
                raise store.PortalError(403, "허용된 주소로 접속해 주세요.")
            url = urlsplit(self.path)
            if url.scheme or url.netloc or len(self.path) > 4096:
                raise store.PortalError(400, "요청 주소를 확인해 주세요.")
            path = url.path
            if api:
                return self.integration_dispatch(url)
            if self.command == "GET" and path == "/portal.css":
                return self.respond(
                    200,
                    Path(__file__).with_name("portal.css").read_bytes(),
                    "text/css; charset=utf-8",
                )
            if path in (SSO_START, SSO_CALLBACK):
                if self.command != "GET":
                    store.not_found()
                response = (
                    self.sso_start()
                    if path == SSO_START
                    else self.sso_callback(url.query)
                )
                return self.respond(*response)
            if (
                path == "/login"
                and self.command == "POST"
                and not self.portal.password_login
            ):
                store.not_found()
            token = self.get_token()
            form, files = {}, []
            if self.command == "POST":
                if self.header("Origin") != self.portal.origin:
                    raise store.PortalError(
                        403, "페이지를 연 주소에서 다시 시도해 주세요."
                    )
                if path != "/login":
                    with store.connect(self.portal.db) as conn:
                        if not session_actor(conn, token, self.portal.idle_seconds):
                            raise store.PortalError(401, "다시 로그인해 주세요.")
                form, files = self.read_form(login=(path == "/login"))
                value = form.get("csrf", "")
                if (
                    not token
                    or not isinstance(value, str)
                    or not value.isascii()
                    or not hmac.compare_digest(value, self.csrf(token))
                ):
                    raise store.PortalError(
                        403, "페이지를 새로고침한 뒤 다시 시도해 주세요."
                    )
                retry = (path, token, form)
                if files and self.portal.scanner:
                    # Scan before the write transaction so a slow scanner does not hold the DB lock.
                    self.portal.scanner.scan(files)
            if path == "/login" and self.command == "POST":
                self.portal.throttle_login(form.get("login", ""))
            with store.connect(self.portal.db, write=self.command == "POST") as conn:
                response = self.route(conn, path, url.query, token, form, files)
            if self.session_stale and token:
                with store.connect(self.portal.db, write=True) as conn:
                    auth.touch_session(conn, token)
            if len(response) == 4:
                self.respond(response[0], response[1], response[2], response[3])
            elif len(response) == 3:
                self.respond(response[0], response[1], response[2])
            else:
                self.respond(response[0], response[1])
        except store.PortalError as exc:
            self.discard_rejected_body()
            if api:
                headers = (
                    {"WWW-Authenticate": 'Bearer realm="collection"'}
                    if exc.status == 401
                    else {}
                )
                if exc.status == 429:
                    headers["Retry-After"] = "60"
                return self.json_response(exc.status, {"error": str(exc)}, headers)
            if retry and exc.status == 400:
                page = self.retry_page(*retry, message=str(exc))
                if page:
                    return self.respond(400, page)
            content = f'<p class="alert" role="alert">{pages.e(exc)}</p><p><a href="/">요구 건 목록</a></p>'
            self.respond(
                exc.status,
                pages.layout("요청을 처리할 수 없습니다", content, error=True),
            )
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0)
            if (code & 255) in (5, 6) or str(exc) in (
                "database is locked",
                "database table is locked",
            ):
                self.report_failure(api, 503, "잠시 뒤 다시 시도해 주세요.")
            else:
                logging.exception("포털 저장 오류")
                self.report_failure(api, 500, "운영 담당자에게 알려 주세요.")
        except Exception:
            logging.exception("포털 요청 오류")
            self.report_failure(api, 500, "운영 담당자에게 알려 주세요.")

    def retry_page(self, path, token, form, *, message):
        match = ITEM.fullmatch(path)
        assignment = (
            match and match[1] == "assignments" and match[3] in ("draft", "submit")
        )
        if path != "/requests" and not assignment:
            return None
        try:
            with store.connect(self.portal.db) as conn:
                actor = session_actor(conn, token, self.portal.idle_seconds)
                if not actor:
                    return None
                csrf = self.csrf(token)
                if assignment and match is not None:
                    removed = form.get("remove", [])
                    values = {
                        "body": form.get("body", "")[:100000],
                        "na": form.get("na"),
                        "remove": removed if isinstance(removed, list) else [],
                    }
                    values["meta"] = {
                        name: str(form.get(name, ""))[:1000]
                        for name in gongmun.META_FIELDS
                    }
                    values["meta"]["personal_info"] = form.get("personal_info") == "1"
                    values["answers"] = {
                        int(m[1]): str(value)[: questions.MAX_ANSWER]
                        for key, value in form.items()
                        if isinstance(key, str)
                        and (m := questions.ANSWER_FIELD.fullmatch(key))
                    }
                    return pages.assignment_page(
                        conn, actor, csrf, int(match[2]), values=values, message=message
                    )
                limits = {
                    "team": 80,
                    "title": 300,
                    "requester": 120,
                    "due": 10,
                    "ledger_id": 100,
                    "instructions": 30000,
                    "doc_no": 100,
                    "received_on": 10,
                    "committee": 120,
                    "basis": 200,
                }
                values = {
                    name: form.get(name, "")[:size] for name, size in limits.items()
                }
                return pages.new_request(
                    conn, actor, csrf, values=values, message=message
                )
        except (store.PortalError, sqlite3.OperationalError):
            return None

    def report_failure(self, api, status, message):
        if api:
            self.json_response(status, {"error": message})
        else:
            self.respond(status, pages.layout("요청 처리 오류", message))

    def integration_dispatch(self, url):
        if not self.portal.enable_integrations:
            store.not_found()
        if self.header("Origin") or self.header("Cookie") or url.query or url.fragment:
            raise store.PortalError(
                400, "서버 연동 요청의 헤더와 주소를 확인해 주세요."
            )
        auth = self.header("Authorization")
        creating = self.command == "POST" and url.path == integrations.REQUESTS
        item = integrations.ITEM.fullmatch(url.path) if self.command == "GET" else None
        if not creating and item is None:
            store.not_found()
        data = None
        with store.connect(self.portal.db) as conn:
            client = integrations.authenticate(conn, auth)
            self.verified_actor = integrations.actor_name(client["id"])
            self.portal.throttle_integration(client["id"])
            if item is not None:
                data = integrations.status(conn, client, item[1])
        if data is not None:
            logging.info("연동 상태 조회 %s", client["id"])
            return self.json_response(200, data)
        if not client["can_intake"]:
            raise store.PortalError(403, "이 연동에는 접수 권한이 없습니다.")
        if (
            self.header("Content-Type").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise store.PortalError(415, "application/json으로 접수해 주세요.")
        payload = integrations.parse_payload(self.read_body(integrations.MAX_JSON))
        with store.connect(self.portal.db, write=True) as conn:
            client = integrations.authenticate(conn, auth)
            code, data = integrations.intake(conn, client, payload)
        logging.info("연동 접수 %s %s", client["id"], code)
        return self.json_response(code, data)

    def read_body(self, limit):
        try:
            length = int(self.header("Content-Length") or "-1")
        except ValueError:
            length = -1
        if length < 0 or length > limit or self.header("Transfer-Encoding"):
            raise store.PortalError(413, "입력 또는 첨부 용량을 확인해 주세요.")
        self.body_read_started = True
        body = self.rfile.read(length)
        if len(body) != length:
            raise store.PortalError(400, "요청 본문이 끝나기 전에 연결이 끊겼습니다.")
        return body

    def discard_rejected_body(self):
        # A short, unread body can cause Windows to abort the connection before a rejection is delivered.
        if self.command != "POST" or self.body_read_started:
            return
        try:
            length = int(self.header("Content-Length"))
            if not 0 < length <= 8192 or self.header("Transfer-Encoding"):
                return
            timeout = self.request.gettimeout()
            deadline = monotonic() + 0.25
            try:
                while length and (remaining := deadline - monotonic()) > 0:
                    self.request.settimeout(remaining)
                    data = self.rfile.read1(length)
                    if not data:
                        break
                    length -= len(data)
            finally:
                self.request.settimeout(timeout)
        except (OSError, ValueError, store.PortalError):
            pass

    def read_form(self, *, login=False):
        content_type = self.header("Content-Type")
        limit = (
            MAX_BODY
            if content_type.startswith("multipart/form-data;")
            else 7 * 1024 * 1024
        )
        if login:
            if content_type != "application/x-www-form-urlencoded":
                raise store.PortalError(415, "지원하지 않는 로그인 형식입니다.")
            limit = 8192
        body = self.read_body(limit)
        try:
            if content_type == "application/x-www-form-urlencoded":
                values = parse_qs(
                    body.decode("ascii"),
                    keep_blank_values=True,
                    max_num_fields=MAX_FORM_FIELDS,
                    encoding="utf-8",
                    errors="strict",
                )
                if any(len(value) != 1 for value in values.values()):
                    raise ValueError
                return {key: value[0] for key, value in values.items()}, []
            if content_type.startswith("multipart/form-data;"):
                return self.parse_multipart(content_type, body)
        except (ValueError, TypeError):
            raise store.PortalError(400, "입력 형식을 확인해 주세요.") from None
        raise store.PortalError(415, "지원하지 않는 요청 형식입니다.")

    @staticmethod
    def parse_multipart(content_type, body):
        if "\r" in content_type or "\n" in content_type:
            raise ValueError
        envelope = ("Content-Type: " + content_type + "\r\n\r\n").encode("ascii")
        message = BytesParser(policy=policy.default).parsebytes(envelope + body)
        if not message.is_multipart() or message.defects:
            raise ValueError
        removed = []
        form: dict[str, Union[str, list[str]]] = {"remove": removed}
        files = []
        for index, part in enumerate(message.iter_parts()):
            if (
                index >= MAX_FORM_FIELDS
                or part.get_content_disposition() != "form-data"
                or part.is_multipart()
            ):
                raise ValueError
            name = part.get_param("name", header="content-disposition")
            data = part.get_payload(decode=True)
            if not isinstance(data, bytes):
                raise ValueError
            filename = part.get_filename()
            if name == "files" and filename:
                files.append((filename, data))
            elif name == "files" and not data:
                continue
            elif name == "remove":
                removed.append(data.decode("ascii"))
            elif (
                isinstance(name, str)
                and name not in form
                and (name in MULTIPART_FIELDS or questions.ANSWER_FIELD.fullmatch(name))
            ):
                form[name] = data.decode("utf-8")
            else:
                raise ValueError
        return form, files

    def redirect(self, path, token=None, age=SESSION_SECONDS):
        headers = {"Location": path}
        if token is not None:
            headers["Set-Cookie"] = self.cookie(token, age)
        return 303, b"", "text/plain", headers

    def route(self, conn, path, query, token, form, files) -> Response:
        session = auth.session_row(conn, token, self.portal.idle_seconds)
        actor = session["login"] if session else None
        self.session_stale = (
            bool(session) and int(time()) - session["last_seen"] >= auth.TOUCH_SECONDS
        )
        self.verified_actor = actor
        if self.command == "POST" and path == "/login":
            return self.login(conn, token, form)
        if not actor:
            if self.command == "GET":
                if path in ("/", "/login"):
                    token = token or secrets.token_urlsafe(32)
                    return (
                        200,
                        self.login_page(token),
                        "text/html; charset=utf-8",
                        {"Set-Cookie": self.cookie(token, 600)},
                    )
                if path in ("/requests/new", "/notices") or ITEM.fullmatch(path):
                    return self.redirect("/")
                store.not_found()
            raise store.PortalError(401, "다시 로그인해 주세요.")
        csrf = self.csrf(token)
        if self.command == "GET":
            return self.get_page(conn, path, query, actor, csrf)
        if path == "/logout":
            conn.execute(
                "DELETE FROM sessions WHERE token=?", (store.token_hash(token),)
            )
            store.event(conn, actor, "로그아웃")
            return self.redirect("/", "", 0)
        return self.change(conn, path, actor, form, files)

    def login(self, conn, token, form):
        username, password = form.get("login", "").strip(), form.get("password", "")
        code = form.get("code", "")
        if len(username) > 80 or len(password) > 256 or len(code) > 12:
            return 401, self.login_page(token, auth.FAILED)
        login, message = auth.password_login(
            conn, username, password, code, require_mfa=self.portal.require_mfa
        )
        if not login:
            # The failure counter must be committed, so this is a normal response, not an exception.
            return 401, self.login_page(token, message or auth.FAILED)
        self.verified_actor = login
        return self.redirect("/", auth.create_session(conn, login, "password", token))

    def sso_start(self):
        provider = self.portal.oidc
        if provider is None:
            store.not_found()
        state, nonce, verifier, challenge = oidc.new_state()
        binding = secrets.token_urlsafe(32)
        with store.connect(self.portal.db, write=True) as conn:
            conn.execute("DELETE FROM sso_states WHERE expiry<=?", (int(time()),))
            if conn.execute("SELECT count(*) FROM sso_states").fetchone()[0] >= 5000:
                raise store.PortalError(
                    503, "로그인 요청이 많습니다. 잠시 뒤 다시 시도해 주세요."
                )
            conn.execute(
                "INSERT INTO sso_states VALUES (?, ?, ?, ?, ?)",
                (
                    store.token_hash(state),
                    store.token_hash(binding),
                    nonce,
                    verifier,
                    int(time()) + oidc.STATE_SECONDS,
                ),
            )
        location = provider.authorization_url(
            self.portal.origin + SSO_CALLBACK, state, nonce, challenge
        )
        return (
            303,
            b"",
            "text/plain",
            {
                "Location": location,
                "Set-Cookie": self.sso_cookie(binding, oidc.STATE_SECONDS),
            },
        )

    def sso_callback(self, query):
        provider = self.portal.oidc
        if provider is None:
            store.not_found()
        clear = self.sso_cookie("", 0)
        try:
            values = parse_qs(
                query,
                keep_blank_values=True,
                max_num_fields=10,
                strict_parsing=bool(query),
            )
        except ValueError:
            raise store.PortalError(400, "로그인 응답 주소를 확인해 주세요.") from None
        state = values.get("state", [""])[0]
        code = values.get("code", [""])[0]
        binding = self.get_token(SSO_COOKIE)
        with store.connect(self.portal.db, write=True) as conn:
            row = (
                conn.execute(
                    "SELECT * FROM sso_states WHERE state_hash=?",
                    (store.token_hash(state),),
                ).fetchone()
                if state
                else None
            )
            if row:
                # One use only, even when the rest of the sign-in fails.
                conn.execute(
                    "DELETE FROM sso_states WHERE state_hash=?", (row["state_hash"],)
                )
        failure = pages.layout(
            "기관 계정 로그인 실패",
            '<p class="alert" role="alert">기관 계정 로그인을 확인하지 '
            '못했습니다. 처음부터 다시 로그인해 주세요.</p><p><a href="/">로그인 화면</a></p>',
            error=True,
        )
        if (
            not row
            or row["expiry"] <= int(time())
            or not binding
            or not hmac.compare_digest(row["binding_hash"], store.token_hash(binding))
        ):
            logging.warning("SSO 콜백 거부 id=%s reason=state", self.request_id)
            return 400, failure, "text/html; charset=utf-8", {"Set-Cookie": clear}
        if "error" in values or not code or len(code) > 2048:
            logging.warning("SSO 콜백 거부 id=%s reason=idp-error", self.request_id)
            return 401, failure, "text/html; charset=utf-8", {"Set-Cookie": clear}
        try:
            id_token = provider.exchange(
                code, row["verifier"], self.portal.origin + SSO_CALLBACK
            )
            claims = provider.verify_id_token(id_token, row["nonce"])
        except oidc.OidcError as exc:
            logging.warning("SSO 토큰 거부 id=%s reason=%s", self.request_id, exc)
            return 401, failure, "text/html; charset=utf-8", {"Set-Cookie": clear}
        with store.connect(self.portal.db, write=True) as conn:
            account = conn.execute(
                "SELECT a.login FROM identities i JOIN accounts a ON a.login=i.login "
                "WHERE i.issuer=? AND i.subject=? AND a.enabled=1",
                (claims["iss"], claims["sub"]),
            ).fetchone()
            if not account:
                store.event(
                    conn,
                    "SSO",
                    "SSO 로그인 거부(미연결 계정)",
                    detail=f"{claims['iss']} / {claims['sub']}",
                )
                denied = pages.layout(
                    "포털 계정 없음",
                    '<p class="alert" role="alert">이 기관 계정에 연결된 포털 '
                    "계정이 없습니다. 운영 담당자에게 계정 연결을 요청해 주세요.</p>",
                    error=True,
                )
                return 403, denied, "text/html; charset=utf-8", {"Set-Cookie": clear}
            login = account["login"]
            token = auth.create_session(conn, login, "sso")
        self.verified_actor = login
        # Strict session cookies are not sent on the IdP-initiated redirect chain; a same-site
        # navigation from this page carries them.
        page = (
            '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="0;url=/"><title>로그인 완료</title></head>'
            '<body><p><a href="/">요구 건 목록으로 이동</a></p></body></html>'
        )
        return (
            200,
            page,
            "text/html; charset=utf-8",
            {"Set-Cookie": [self.cookie(token), clear]},
        )

    def get_page(self, conn, path, query, actor, csrf) -> Response:
        if path in ("/", "/notices"):
            try:
                arguments = parse_qs(query, max_num_fields=20)
            except ValueError:
                raise store.PortalError(400, "검색 주소를 확인해 주세요.") from None
            render = pages.home if path == "/" else pages.notice_inbox
            return 200, render(conn, actor, csrf, arguments)
        if path == "/requests/new":
            return 200, pages.new_request(conn, actor, csrf)
        match = ITEM.fullmatch(path)
        if not match:
            store.not_found()
        group, raw_id, action = match.groups()
        object_id = int(raw_id)
        if group == "requests" and not action:
            return 200, pages.request_page(conn, actor, csrf, object_id)
        if group == "requests" and action in ("template", "document"):
            download = self.request_document(conn, actor, object_id, action)
            return logged_download(actor, group, object_id, action, download)
        if group == "assignments" and not action:
            return 200, pages.assignment_page(conn, actor, csrf, object_id)
        if group == "attachments" and not action:
            return logged_download(
                actor,
                group,
                object_id,
                "attachment",
                self.attachment(conn, actor, object_id),
            )
        if group == "finals" and action == "download":
            return logged_download(
                actor,
                group,
                object_id,
                action,
                self.final_export(conn, actor, object_id),
            )
        if group == "finals" and action == "document":
            return logged_download(
                actor,
                group,
                object_id,
                action,
                self.final_document(conn, actor, object_id),
            )
        if group == "finals" and action == "letter":
            final, request = self.final_for(conn, actor, object_id)
            letter = final_letter(conn, final, request)
            return (
                200,
                letter.encode("utf-8"),
                "text/plain; charset=utf-8",
                download_headers(
                    f"request-{request['id']}-v{final['revision']}-제출공문초안.txt"
                ),
            )
        store.not_found()

    def change(self, conn, path, actor, form, files) -> Response:
        if path == "/requests":
            request_id = store.create_request(conn, actor, form, files)
            return self.redirect(f"/requests/{request_id}")
        match = ITEM.fullmatch(path)
        if not match:
            store.not_found()
        group, raw_id, action = match.groups()
        object_id = int(raw_id)
        if group == "notices" and action == "open":
            return self.redirect(notices.open_notice(conn, actor, object_id))
        if group == "requests" and action in ("announce", "remind"):
            if files:
                raise store.PortalError(400, "업무 공지는 메시지만 입력해 주세요.")
            if action == "remind":
                request, manager, _ = store.request_for(conn, actor, object_id)
                if not manager:
                    store.not_found()
                store.remind_pending(conn, actor, request, form)
            else:
                notices.publish(conn, actor, object_id, form)
            return self.redirect(f"/requests/{object_id}#request-notices")
        if group == "assignments" and action in ("cancel", "reschedule", "dispute"):
            if files:
                raise store.PortalError(400, "이 작업에는 파일을 첨부할 수 없습니다.")
            row, request, manager, own = store.assignment_for(conn, actor, object_id)
            if (action == "dispute" and not own) or (
                action != "dispute" and not manager
            ):
                store.not_found()
            store.check_state(request["status"] == "collecting")
            if action == "dispute":
                store.dispute_assignment(conn, actor, row, request, form)
            else:
                store.manage_assignment(conn, actor, row, request, form, action)
            return self.redirect(f"/assignments/{object_id}")
        if group == "requests" and action in (
            "assign",
            "prepare",
            "finalize",
            "sent",
            "ledger",
            "reopen",
            "questions",
            "route",
            "unquestion",
        ):
            request, manager, _ = store.request_for(conn, actor, object_id)
            collector_actions = (
                "assign",
                "prepare",
                "questions",
                "route",
                "unquestion",
            )
            allowed = (
                manager
                if action in collector_actions
                else store.team_allowed(conn, actor, request["team"], lead=True)
            )
            if not allowed:
                store.not_found()
            if files and action not in ("prepare", "finalize"):
                raise store.PortalError(400, "이 작업에는 파일을 첨부할 수 없습니다.")
            store.change_request(conn, actor, request, form, action, files)
            return self.redirect(f"/requests/{object_id}")
        if group == "assignments" and action in (
            "draft",
            "submit",
            "approve",
            "return",
        ):
            row, request, manager, own = store.assignment_for(conn, actor, object_id)
            writing = action in ("draft", "submit")
            if (writing and not own) or (not writing and not manager):
                store.not_found()
            store.check_state(request["status"] == "collecting")
            if writing:
                store.change_draft(conn, actor, row, form, files, action)
            else:
                if files:
                    raise store.PortalError(
                        400, "이 작업에는 파일을 첨부할 수 없습니다."
                    )
                store.review(conn, actor, row, request, form, action)
            return self.redirect(f"/assignments/{object_id}")
        store.not_found()

    @staticmethod
    def attachment(conn, actor, attachment_id):
        row = store.require_row(
            conn.execute(
                "SELECT assignment, submission, name FROM attachments WHERE id=?",
                (attachment_id,),
            ).fetchone()
        )
        _, _, _, own = store.assignment_for(conn, actor, row["assignment"])
        if row["submission"] is None and not own:
            store.not_found()
        data = conn.execute(
            "SELECT content FROM attachments WHERE id=?", (attachment_id,)
        ).fetchone()[0]
        return 200, data, "application/octet-stream", download_headers(row["name"])

    @staticmethod
    def request_document(conn, actor, request_id, action):
        _, manager, _ = store.request_for(conn, actor, request_id)
        if action == "document":
            if not manager:
                store.not_found()
            row = conn.execute(
                "SELECT name, content FROM draft_documents WHERE request_id=?",
                (request_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT name, content FROM request_documents WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return document_response(store.require_row(row))

    @staticmethod
    def final_for(conn, actor, final_id):
        source = store.require_row(
            conn.execute(
                "SELECT request_id FROM finals WHERE id=?", (final_id,)
            ).fetchone()
        )
        request, _, _ = store.request_for(conn, actor, source["request_id"])
        if not store.team_allowed(conn, actor, request["team"], lead=True):
            store.not_found()
        final = store.require_row(
            conn.execute("SELECT * FROM finals WHERE id=?", (final_id,)).fetchone()
        )
        return final, request

    def final_document(self, conn, actor, final_id):
        self.final_for(conn, actor, final_id)
        row = conn.execute(
            "SELECT name, content FROM final_documents WHERE final_id=?", (final_id,)
        ).fetchone()
        return document_response(store.require_row(row))

    def final_export(self, conn, actor, final_id):
        final, request = self.final_for(conn, actor, final_id)
        files = conn.execute(ATTACHMENT_SQL, (final_id,)).fetchall()
        native = conn.execute(
            "SELECT name, sha256, length(content) AS size FROM final_documents WHERE final_id=?",
            (final_id,),
        ).fetchone()
        if (
            sum(item["size"] for item in files) + (native["size"] if native else 0)
            > 100 * documents.MIB
        ):
            raise store.PortalError(
                413,
                "첨부 묶음이 100 MiB를 넘습니다. 제출본에서 개별 파일을 내려받아 주세요.",
            )
        sources = final_sources(conn, final_id)
        manifest = [
            f"요구 건 {request['id']} · {final['revision']}판",
            f"확정 시각 {final['created']}",
        ]
        manifest.extend(
            f"{s['name']} 제출 {s['revision']}판 · {gongmun.describe(store.load_meta(s['meta']))}"
            for s in sources
        )
        if final["exception"]:
            manifest.append("예외 승인 " + final["exception"])
        with SpooledTemporaryFile(max_size=1024 * 1024) as buffer:
            with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
                if final["body"]:
                    archive.writestr(
                        "최종답변.txt", request["title"] + "\n\n" + final["body"] + "\n"
                    )
                if native:
                    entry = "최종본/" + native["name"]
                    content = conn.execute(
                        "SELECT content FROM final_documents WHERE final_id=?",
                        (final_id,),
                    ).fetchone()[0]
                    archive.writestr(entry, content)
                    manifest.append(f"{entry} · 확정 한글 문서 · {native['sha256']}")
                for item in files:
                    entry = f"첨부/{item['id']}-{item['name']}"
                    content = conn.execute(
                        "SELECT content FROM attachments WHERE id=?", (item["id"],)
                    ).fetchone()[0]
                    archive.writestr(entry, content)
                    manifest.append(
                        f"{entry} · {item['department']} {item['revision']}판 · {item['sha256']}"
                    )
                archive.writestr("취합근거.txt", "\n".join(manifest) + "\n")
                archive.writestr(
                    "제출공문_초안.txt", final_letter(conn, final, request)
                )
                if questions.for_request(conn, request["id"]):
                    archive.writestr(
                        "질의별답변.txt",
                        questions.compiled_text(
                            conn, request["id"], final_submissions(conn, final_id)
                        ),
                    )
            buffer.seek(0)
            data = buffer.read()
        return (
            200,
            data,
            "application/zip",
            download_headers(f"request-{request['id']}-v{final['revision']}.zip"),
        )


def final_submissions(conn, final_id):
    """확정 판에 고정된 승인 제출본(`store.approved_sources`와 같은 열)."""
    return conn.execute(
        "SELECT s.*, d.name AS department_name FROM final_sources f JOIN submissions s ON s.id=f.submission "
        "JOIN assignments p ON p.id=s.assignment JOIN scopes d ON d.id=p.department "
        "WHERE f.final=? ORDER BY p.id",
        (final_id,),
    ).fetchall()


def final_sources(conn, final_id):
    return conn.execute(
        "SELECT d.name, s.revision, s.meta FROM final_sources f JOIN submissions s ON s.id=f.submission "
        "JOIN assignments p ON p.id=s.assignment JOIN scopes d ON d.id=p.department "
        "WHERE f.final=? ORDER BY p.id",
        (final_id,),
    ).fetchall()


def final_letter(conn, final, request):
    """제출 공문 기안 초안. 붙임은 확정 한글 문서(없으면 답변서)와 승인 첨부 순서다."""
    attachments = []
    native = conn.execute(
        "SELECT name FROM final_documents WHERE final_id=?", (final["id"],)
    ).fetchone()
    if native:
        attachments.append(native["name"])
    elif final["body"]:
        attachments.append(f"{request['title']} 답변서")
    attachments.extend(
        row["name"] for row in conn.execute(ATTACHMENT_SQL, (final["id"],))
    )
    restricted = [
        (row["name"], meta)
        for row in final_sources(conn, final["id"])
        if gongmun.restricted(meta := store.load_meta(row["meta"]))
    ]
    return gongmun.cover_letter(request, attachments, restricted)


def download_headers(name):
    return {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe="")
    }


def document_response(row):
    return (
        200,
        row["content"],
        "application/octet-stream",
        download_headers(row["name"]),
    )


def session_actor(conn, token, idle=auth.IDLE_SECONDS):
    row = auth.session_row(conn, token, idle)
    return row["login"] if row else None


def logged_download(actor, group, object_id, action, download):
    logging.info(
        "다운로드 요청 승인 actor=%s resource=%s id=%s action=%s",
        actor,
        group,
        object_id,
        action,
    )
    return download
