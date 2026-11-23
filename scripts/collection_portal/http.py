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

from . import documents, integrations, notices, pages, store

COOKIE = "collection_session"
SESSION_SECONDS = 8 * 3600
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
ITEM = re.compile(
    r"/(requests|assignments|attachments|finals|notices)/([1-9][0-9]{0,17})(?:/([a-z]+))?"
)
MAX_BODY = documents.MAX_SUBMISSION_FILES_SIZE + 7 * documents.MIB
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
    def __init__(self, address, db, origin=None, *, enable_integrations=False):
        self.db = db
        self.secret = secrets.token_bytes(32)
        self.login_attempts = []
        self.integration_attempts = {}
        self.enable_integrations = enable_integrations
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
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def cookie(self, token, age=SESSION_SECONDS):
        secure = "; Secure" if self.portal.secure_cookie else ""
        return f"{self.cookie_name()}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"

    def cookie_name(self):
        return "__Host-" + COOKIE if self.portal.secure_cookie else COOKIE

    def get_token(self):
        try:
            cookie = SimpleCookie(self.header("Cookie")).get(self.cookie_name())
        except CookieError:
            return ""
        return cookie.value if cookie and TOKEN.fullmatch(cookie.value) else ""

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
            token = self.get_token()
            form, files = {}, []
            if self.command == "POST":
                if self.header("Origin") != self.portal.origin:
                    raise store.PortalError(
                        403, "페이지를 연 주소에서 다시 시도해 주세요."
                    )
                if path != "/login":
                    with store.connect(self.portal.db) as conn:
                        if not session_actor(conn, token):
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
            if path == "/login" and self.command == "POST":
                self.portal.throttle_login(form.get("login", ""))
            with store.connect(self.portal.db, write=self.command == "POST") as conn:
                response = self.route(conn, path, url.query, token, form, files)
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
                actor = session_actor(conn, token)
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
                    max_num_fields=30,
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
                index >= 25
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
            elif name in MULTIPART_FIELDS and name not in form:
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
        actor = session_actor(conn, token)
        self.verified_actor = actor
        if self.command == "POST" and path == "/login":
            return self.login(conn, token, form)
        if not actor:
            if self.command == "GET":
                if path in ("/", "/login"):
                    token = token or secrets.token_urlsafe(32)
                    return (
                        200,
                        pages.login(self.csrf(token)),
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
        if (
            len(username) > 80
            or len(password) > 256
            or not store.verify_login(conn, username, password)
        ):
            return 401, pages.login(
                self.csrf(token), "계정 또는 비밀번호를 확인해 주세요."
            )
        conn.execute(
            "DELETE FROM sessions WHERE expiry<=? OR token=?",
            (int(time()), store.token_hash(token)),
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (store.token_hash(token), username, int(time()) + SESSION_SECONDS),
        )
        self.verified_actor = username
        store.event(conn, username, "로그인")
        return self.redirect("/", token)

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
        if group == "requests" and action == "announce":
            if files:
                raise store.PortalError(400, "업무 공지는 메시지만 입력해 주세요.")
            notices.publish(conn, actor, object_id, form)
            return self.redirect(f"/requests/{object_id}#request-notices")
        if group == "requests" and action in (
            "assign",
            "prepare",
            "finalize",
            "sent",
            "ledger",
            "reopen",
        ):
            request, manager, _ = store.request_for(conn, actor, object_id)
            allowed = (
                manager
                if action in ("assign", "prepare")
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
        sources = conn.execute(
            "SELECT d.name, s.revision FROM final_sources f JOIN submissions s ON s.id=f.submission "
            "JOIN assignments p ON p.id=s.assignment JOIN scopes d ON d.id=p.department "
            "WHERE f.final=? ORDER BY p.id",
            (final_id,),
        ).fetchall()
        manifest = [
            f"요구 건 {request['id']} · {final['revision']}판",
            f"확정 시각 {final['created']}",
        ]
        manifest.extend(f"{s['name']} 제출 {s['revision']}판" for s in sources)
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
            buffer.seek(0)
            data = buffer.read()
        return (
            200,
            data,
            "application/zip",
            download_headers(f"request-{request['id']}-v{final['revision']}.zip"),
        )


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


def session_actor(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT s.login FROM sessions s JOIN accounts a ON a.login=s.login "
        "WHERE token=? AND expiry>? AND a.enabled=1",
        (store.token_hash(token), int(time())),
    ).fetchone()
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
