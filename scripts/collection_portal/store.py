"""Storage, authorization and workflow for the collection portal."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import NoReturn, Optional, TypeVar

from . import documents, gongmun, questions

KST = timezone(timedelta(hours=9))
APPLICATION_ID = 0x434F4C50
SCHEMA_VERSION = 7
MAX_FILES = 5
ROLES = ("department", "collector", "lead")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scopes (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL
        CHECK(kind IN ('department', 'team'))
);
CREATE TABLE IF NOT EXISTS accounts (
    login TEXT PRIMARY KEY, password TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS grants (
    login TEXT REFERENCES accounts(login), scope TEXT REFERENCES scopes(id),
    role TEXT NOT NULL CHECK(role IN ('department', 'collector', 'lead')),
    PRIMARY KEY(login, scope, role)
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, login TEXT NOT NULL REFERENCES accounts(login),
    expiry INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY, team TEXT NOT NULL REFERENCES scopes(id), title TEXT NOT NULL,
    requester TEXT NOT NULL, instructions TEXT NOT NULL, due TEXT NOT NULL,
    ledger_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'collecting',
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS collection_drafts (
    request_id INTEGER PRIMARY KEY REFERENCES requests(id), body TEXT NOT NULL,
    stamp TEXT NOT NULL, actor TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
    department TEXT NOT NULL REFERENCES scopes(id), due TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', version INTEGER NOT NULL DEFAULT 1,
    draft TEXT NOT NULL DEFAULT '', na INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', UNIQUE(request_id, department)
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY, assignment INTEGER NOT NULL REFERENCES assignments(id),
    revision INTEGER NOT NULL, body TEXT NOT NULL, na INTEGER NOT NULL,
    actor TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(assignment, revision)
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY, assignment INTEGER NOT NULL REFERENCES assignments(id),
    submission INTEGER REFERENCES submissions(id), name TEXT NOT NULL, sha256 TEXT NOT NULL,
    content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS finals (
    id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
    revision INTEGER NOT NULL, body TEXT NOT NULL, exception TEXT NOT NULL,
    actor TEXT NOT NULL, created TEXT NOT NULL, sent_on TEXT, sent_by TEXT,
    ledger_state TEXT NOT NULL DEFAULT 'unrecorded', UNIQUE(request_id, revision)
);
CREATE TABLE IF NOT EXISTS final_sources (
    final INTEGER REFERENCES finals(id), submission INTEGER REFERENCES submissions(id),
    PRIMARY KEY(final, submission)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, request_id INTEGER REFERENCES requests(id),
    assignment INTEGER REFERENCES assignments(id), actor TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attachment_submission ON attachments(submission);
CREATE INDEX IF NOT EXISTS attachment_assignment ON attachments(assignment, submission);
CREATE INDEX IF NOT EXISTS event_request ON events(request_id, id);
"""

DOCUMENT_SCHEMA = """
CREATE TABLE request_documents (
    request_id INTEGER PRIMARY KEY REFERENCES requests(id),
    name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL
);
CREATE TABLE draft_documents (
    request_id INTEGER PRIMARY KEY REFERENCES requests(id),
    name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL
);
CREATE TABLE final_documents (
    final_id INTEGER PRIMARY KEY REFERENCES finals(id),
    name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL
);
"""

INTEGRATION_SCHEMA = """
CREATE TABLE integration_clients (
    id TEXT PRIMARY KEY, team TEXT NOT NULL REFERENCES scopes(id),
    can_intake INTEGER NOT NULL CHECK(can_intake IN (0, 1)),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);
CREATE TABLE integration_keys (
    id TEXT PRIMARY KEY, client TEXT NOT NULL REFERENCES integration_clients(id),
    token_hash TEXT NOT NULL UNIQUE, issued INTEGER NOT NULL, expires INTEGER NOT NULL,
    revoked INTEGER
);
CREATE TABLE integration_requests (
    client TEXT NOT NULL REFERENCES integration_clients(id), external_id TEXT NOT NULL,
    request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id), fingerprint TEXT NOT NULL,
    PRIMARY KEY(client, external_id)
);
"""

NOTICE_SCHEMA = """
CREATE TABLE notices (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    audience TEXT NOT NULL CHECK(audience IN ('team','department')),
    message TEXT NOT NULL
);
CREATE TABLE notice_assignments (
    event_id INTEGER NOT NULL REFERENCES notices(event_id),
    assignment INTEGER NOT NULL REFERENCES assignments(id),
    PRIMARY KEY(event_id, assignment)
);
CREATE TABLE notice_reads (
    event_id INTEGER NOT NULL REFERENCES notices(event_id),
    login TEXT NOT NULL REFERENCES accounts(login),
    read_at TEXT NOT NULL,
    PRIMARY KEY(event_id, login)
);
"""

# Schema 5: account lockout, MFA, idle sessions, SSO identities and a tamper-evident audit chain.
SECURITY_COLUMNS = (
    "ALTER TABLE accounts ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN locked_until INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN totp_secret TEXT",
    "ALTER TABLE accounts ADD COLUMN totp_step INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN created INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN method TEXT NOT NULL DEFAULT 'password'",
    "ALTER TABLE events ADD COLUMN chain TEXT NOT NULL DEFAULT ''",
)

SECURITY_SCHEMA = """
CREATE TABLE identities (
    issuer TEXT NOT NULL, subject TEXT NOT NULL,
    login TEXT NOT NULL REFERENCES accounts(login), created TEXT NOT NULL,
    PRIMARY KEY(issuer, subject)
);
CREATE INDEX identity_login ON identities(login);
CREATE TABLE sso_states (
    state_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL, nonce TEXT NOT NULL,
    verifier TEXT NOT NULL, expiry INTEGER NOT NULL
);
CREATE INDEX session_login ON sessions(login);
"""

CHAIN_GENESIS = "0" * 64

# Schema 6: official-document metadata of a request and of each department reply.
WORKFLOW_COLUMNS = (
    "ALTER TABLE requests ADD COLUMN doc_no TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN received_on TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN committee TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN basis TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE assignments ADD COLUMN draft_meta TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE submissions ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'",
)
# Assignment states. 'disputed' = 부서가 소관 이의를 제기, 'cancelled' = 취합팀이 배정 취소.
WORKING_STATES = ("open", "draft", "returned")
ACTIVE = "status<>'cancelled'"


class PortalError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


def today():
    return datetime.now(KST).date().isoformat()


@contextmanager
def connect(path, write=False):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    with connect(path, write=True) as conn:
        kind = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if kind == APPLICATION_ID:
            if version == SCHEMA_VERSION:
                return
            if not 1 <= version < SCHEMA_VERSION:
                raise PortalError(
                    400, "현재 포털에서 지원하는 DB 버전인지 확인해 주세요."
                )
        else:
            tables = conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            if kind or version or tables:
                raise PortalError(400, "별도의 새 포털 DB 경로를 지정해 주세요.")
            create_tables(conn, SCHEMA)
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        if version < 2:
            create_tables(conn, DOCUMENT_SCHEMA)
        if version < 3:
            create_tables(conn, INTEGRATION_SCHEMA)
        if version < 4:
            create_tables(conn, NOTICE_SCHEMA)
        if version < 5:
            migrate_security(conn)
        if version < 6:
            for statement in WORKFLOW_COLUMNS:
                conn.execute(statement)
        if version < 7:
            create_tables(conn, questions.QUESTION_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def migrate_security(conn):
    for statement in SECURITY_COLUMNS:
        conn.execute(statement)
    create_tables(conn, SECURITY_SCHEMA)
    # Old sessions carry no activity time, so the idle limit cannot be applied to them.
    conn.execute("DELETE FROM sessions")
    previous = CHAIN_GENESIS
    for row in conn.execute("SELECT * FROM events ORDER BY id").fetchall():
        previous = chain_value(previous, row)
        conn.execute("UPDATE events SET chain=? WHERE id=?", (previous, row["id"]))


def create_tables(conn, schema):
    for statement in schema.strip().rstrip(";").split(";"):
        conn.execute(statement)


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def verify_login(conn, login, password):
    """Password check only. Lockout and MFA are applied by `auth.password_login`."""
    row = conn.execute("SELECT * FROM accounts WHERE login=?", (login,)).fetchone()
    if row:
        salt = bytes.fromhex(row["password"].split(":")[0])
        matched = hmac.compare_digest(row["password"], password_hash(password, salt))
        return matched and bool(row["enabled"])
    password_hash(password, bytes(16))
    return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def chain_value(previous, row):
    fields = [
        row["id"],
        row["request_id"],
        row["assignment"],
        row["actor"],
        row["action"],
        row["detail"],
        row["created"],
    ]
    canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((previous + canonical).encode("utf-8")).hexdigest()


def event(
    conn,
    actor,
    action,
    request_id=None,
    assignment=None,
    detail="",
    *,
    notify=None,
    message="",
    targets=None,
):
    event_id = conn.execute(
        "INSERT INTO events(request_id, assignment, actor, action, detail, created) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (request_id, assignment, actor, action, detail, now()),
    ).lastrowid
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if "chain" in row.keys():  # Pre-5 rows are chained by `migrate_security`.
        # Writers hold BEGIN IMMEDIATE, so the previous link cannot change under this insert.
        previous = conn.execute(
            "SELECT chain FROM events WHERE id<? ORDER BY id DESC LIMIT 1", (event_id,)
        ).fetchone()
        conn.execute(
            "UPDATE events SET chain=? WHERE id=?",
            (chain_value(previous[0] if previous else CHAIN_GENESIS, row), event_id),
        )
    if notify == "team":
        conn.execute("INSERT INTO notices VALUES (?, 'team', ?)", (event_id, message))
    elif notify in ("assignment", "assigned"):
        conn.execute(
            "INSERT INTO notices VALUES (?, 'department', ?)", (event_id, message)
        )
        if targets is not None:
            conn.executemany(
                "INSERT INTO notice_assignments VALUES (?, ?)",
                [(event_id, target) for target in targets],
            )
        else:
            # Request-wide notices skip cancelled assignments; a targeted notice reaches its assignment.
            conn.execute(
                "INSERT INTO notice_assignments SELECT ?, id FROM assignments "
                "WHERE request_id=? AND ((? IS NULL AND status<>'cancelled') OR id=?)",
                (event_id, request_id, assignment, assignment),
            )


def required(value, label, limit):
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise PortalError(400, f"{label}은(는) 1~{limit}자로 입력해 주세요.")
    return value


def calendar_date(value):
    try:
        parsed = date.fromisoformat(value).isoformat()
        if parsed == value:
            return parsed
    except ValueError:
        pass
    raise PortalError(400, "날짜를 입력해 주세요.")


ACTOR_GRANTS = """
WITH actor_grants AS (
    SELECT g.scope, g.role FROM grants g JOIN accounts u ON u.login=g.login
    WHERE g.login=:actor AND u.enabled=1
)
"""

VISIBLE_REQUESTS = (
    ACTOR_GRANTS
    + """
, request_access AS (
    SELECT r.id AS request_id, 1 AS team_access FROM requests r JOIN actor_grants g
        ON g.scope=r.team AND g.role IN ('collector','lead')
    WHERE (:request_id IS NULL OR r.id=:request_id)
    UNION ALL
    SELECT a.request_id, 0 FROM assignments a JOIN actor_grants g
        ON g.scope=a.department AND g.role='department'
    WHERE (:request_id IS NULL OR a.request_id=:request_id)
), visible_requests AS (
    SELECT r.*, s.team_access FROM requests r JOIN (
        SELECT request_id, max(team_access) AS team_access FROM request_access GROUP BY request_id
    ) s ON s.request_id=r.id
)
"""
)

# The same audience predicate protects the inbox, read receipts, and notice search.
NOTICE_AUDIENCE = """
((n.audience='team' AND EXISTS (
    SELECT 1 FROM actor_grants g WHERE g.scope=r.team AND g.role IN ('collector','lead')
)) OR (n.audience='department' AND EXISTS (
    SELECT 1 FROM notice_assignments target JOIN assignments a ON a.id=target.assignment
    JOIN actor_grants g ON g.scope=a.department AND g.role='department'
    WHERE target.event_id=n.event_id AND a.request_id=r.id
)))
"""


def visible_grants(conn, actor):
    return conn.execute(
        ACTOR_GRANTS
        + "SELECT g.scope, g.role, s.name FROM actor_grants g JOIN scopes s ON s.id=g.scope "
        "ORDER BY s.name, g.role",
        {"actor": actor},
    ).fetchall()


def team_allowed(conn, actor, team, lead=False):
    return (
        conn.execute(
            ACTOR_GRANTS + "SELECT 1 FROM actor_grants WHERE scope=:team "
            "AND (role='lead' OR (role='collector' AND :lead=0))",
            {"actor": actor, "team": team, "lead": lead},
        ).fetchone()
        is not None
    )


def assigned(conn, actor, request_id):
    return conn.execute(
        ACTOR_GRANTS + "SELECT a.*, s.name AS department_name FROM assignments a "
        "JOIN scopes s ON s.id=a.department "
        "JOIN actor_grants g ON g.scope=a.department AND g.role='department' "
        "WHERE a.request_id=:request_id ORDER BY a.id",
        {"request_id": request_id, "actor": actor},
    ).fetchall()


Row = TypeVar("Row")


def not_found() -> NoReturn:
    raise PortalError(404, "항목을 찾을 수 없습니다.")


def require_row(row: Optional[Row]) -> Row:
    if row is None:
        not_found()
    return row


def request_for(conn, actor, request_id):
    row = require_row(
        conn.execute(
            VISIBLE_REQUESTS + "SELECT * FROM visible_requests WHERE id=:request_id",
            {"actor": actor, "request_id": request_id},
        ).fetchone()
    )
    return row, bool(row["team_access"]), assigned(conn, actor, request_id)


def search_requests(conn, actor, keyword, status, *, limit, offset):
    # A shared request may match only the submission rows or drafts the actor can open.
    return conn.execute(
        VISIBLE_REQUESTS
        + """
        SELECT r.* FROM visible_requests r
        WHERE (:status='all' OR (:status='active' AND r.status<>'sent')
            OR (:status='done' AND r.status='sent'))
        AND (:keyword=''
            OR instr(lower(r.title), lower(:keyword))>0 OR instr(lower(r.requester), lower(:keyword))>0
            OR instr(lower(r.instructions), lower(:keyword))>0
            OR (r.team_access=1 AND EXISTS (SELECT 1 FROM questions q
                WHERE q.request_id=r.id AND instr(lower(q.body), lower(:keyword))>0))
            OR EXISTS (SELECT 1 FROM request_documents d
                WHERE d.request_id=r.id AND instr(lower(d.name), lower(:keyword))>0)
            OR EXISTS (
                SELECT 1 FROM events evt JOIN notices n ON n.event_id=evt.id
                WHERE evt.request_id=r.id AND (r.team_access=1 OR """
        + NOTICE_AUDIENCE
        + """)
                AND (instr(lower(n.message), lower(:keyword))>0 OR instr(lower(evt.action), lower(:keyword))>0)
            )
            OR EXISTS (
                SELECT 1 FROM assignments a
                LEFT JOIN actor_grants g ON g.role='department' AND g.scope=a.department
                WHERE a.request_id=r.id AND (r.team_access=1 OR g.scope IS NOT NULL)
                AND (
                    (g.scope IS NOT NULL AND instr(lower(a.draft), lower(:keyword))>0)
                    OR EXISTS (SELECT 1 FROM submissions s
                        WHERE s.assignment=a.id AND instr(lower(s.body), lower(:keyword))>0)
                    OR EXISTS (SELECT 1 FROM submissions s JOIN submission_answers x ON x.submission=s.id
                        WHERE s.assignment=a.id AND instr(lower(x.body), lower(:keyword))>0)
                    OR (g.scope IS NOT NULL AND EXISTS (SELECT 1 FROM draft_answers d
                        WHERE d.assignment=a.id AND instr(lower(d.body), lower(:keyword))>0))
                    OR EXISTS (SELECT 1 FROM question_assignments m JOIN questions q ON q.id=m.question
                        WHERE m.assignment=a.id AND instr(lower(q.body), lower(:keyword))>0)
                    OR EXISTS (SELECT 1 FROM attachments f WHERE f.assignment=a.id
                        AND (f.submission IS NOT NULL OR g.scope IS NOT NULL)
                        AND instr(lower(f.name), lower(:keyword))>0)
                )
            ))
        ORDER BY (r.status='sent'), r.due, r.id DESC LIMIT :limit OFFSET :offset
        """,
        {
            "actor": actor,
            "request_id": None,
            "keyword": keyword,
            "status": status,
            "limit": limit,
            "offset": offset,
        },
    ).fetchall()


def assignment_for(conn, actor, assignment_id):
    request_id = require_row(
        conn.execute(
            "SELECT request_id FROM assignments WHERE id=?", (assignment_id,)
        ).fetchone()
    )
    request, manager, own = request_for(conn, actor, request_id["request_id"])
    own_row = next((item for item in own if item["id"] == assignment_id), None)
    if own_row:
        return own_row, request, manager, True
    if not manager:
        not_found()
    row = require_row(
        conn.execute(
            "SELECT a.*, s.name AS department_name FROM assignments a JOIN scopes s "
            "ON s.id=a.department WHERE a.id=?",
            (assignment_id,),
        ).fetchone()
    )
    return row, request, manager, False


def check_version(row, form):
    if form.get("version") != str(row["version"]):
        raise PortalError(
            409, "다른 작업으로 내용이 바뀌었습니다. 화면을 다시 열어 주세요."
        )


def check_state(allowed):
    if not allowed:
        raise PortalError(409, "현재 상태에서 이 작업을 할 수 없습니다.")


def create_request(conn, actor, form, files=()):
    team = form.get("team", "")
    if not team_allowed(conn, actor, team):
        not_found()
    return insert_request(conn, actor, team, form, files)


def insert_request(conn, actor, team, form, files=()):
    ledger_id = form.get("ledger_id", "").strip()
    if len(ledger_id) > 100:
        raise PortalError(400, "대장 ID는 100자 이내로 입력해 주세요.")
    due = calendar_date(form.get("due", ""))
    received = form.get("received_on", "").strip()
    received = calendar_date(received) if received else ""
    if received and received > due:
        raise PortalError(400, "접수일은 대외 마감일보다 늦을 수 없습니다.")
    optional = {}
    for name, label, limit in (
        ("doc_no", "요구 문서번호", 100),
        ("committee", "수신(요구 기관)", 120),
        ("basis", "요구 근거", 200),
    ):
        optional[name] = form.get(name, "").strip()
        if len(optional[name]) > limit:
            raise PortalError(400, f"{label}은(는) {limit}자 이내로 입력해 주세요.")
    request_id = conn.execute(
        "INSERT INTO requests(team, title, requester, instructions, due, ledger_id, doc_no, received_on, "
        "committee, basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            team,
            required(form.get("title", ""), "제목", 300),
            required(form.get("requester", ""), "요구자", 120),
            required(form.get("instructions", ""), "요구 내용", 30000),
            due,
            ledger_id,
            optional["doc_no"],
            received,
            optional["committee"],
            optional["basis"],
        ),
    ).lastrowid
    document = single_hancom_document(files)
    if document:
        conn.execute(
            "INSERT INTO request_documents VALUES (?, ?, ?, ?)", (request_id, *document)
        )
    event(conn, actor, "요구 건 등록", request_id, notify="team")
    return request_id


def add_assignment(conn, actor, request, form):
    department = form.get("department", "")
    require_row(
        conn.execute(
            "SELECT id FROM scopes WHERE id=? AND kind='department'", (department,)
        ).fetchone()
    )
    due = calendar_date(form.get("due", ""))
    if due > request["due"]:
        raise PortalError(400, "내부 마감일은 대외 마감일 이내여야 합니다.")
    cancelled = conn.execute(
        "SELECT id FROM assignments WHERE request_id=? AND department=? AND status='cancelled'",
        (request["id"], department),
    ).fetchone()
    if cancelled:
        # 취소했던 부서를 다시 배정하면 기존 행을 살려 이력과 제출본을 이어 간다.
        conn.execute(
            "UPDATE assignments SET status=CASE WHEN draft<>'' THEN 'draft' ELSE 'open' END, "
            "due=?, note='', version=version+1 WHERE id=?",
            (due, cancelled["id"]),
        )
        assignment_id = cancelled["id"]
    else:
        created = conn.execute(
            "INSERT INTO assignments(request_id, department, due) VALUES (?, ?, ?) "
            "ON CONFLICT(request_id, department) DO NOTHING",
            (request["id"], department, due),
        )
        check_state(created.rowcount == 1)
        assignment_id = created.lastrowid
    set_request_state(conn, request, "collecting")
    event(
        conn,
        actor,
        "부서 배정",
        request["id"],
        assignment_id,
        department,
        notify="assignment",
    )


def manage_assignment(conn, actor, row, request, form, action):
    """취합팀의 배정 취소(cancel)와 내부 마감 변경·소관 이의 회신(reschedule)."""
    check_version(row, form)
    if action == "cancel":
        check_state(row["status"] not in ("approved", "cancelled"))
        reason = required(form.get("note", ""), "배정 취소 사유", 1000)
        conn.execute(
            "UPDATE assignments SET status='cancelled', note=?, version=version+1 WHERE id=?",
            (reason, row["id"]),
        )
        event(
            conn,
            actor,
            "배정 취소",
            request["id"],
            row["id"],
            reason,
            notify="assignment",
            message=reason,
        )
        return
    check_state(row["status"] in (*WORKING_STATES, "disputed"))
    due = calendar_date(form.get("due", ""))
    if due > request["due"]:
        raise PortalError(400, "내부 마감일은 대외 마감일 이내여야 합니다.")
    note = form.get("note", "").strip()
    if len(note) > 1000:
        raise PortalError(400, "안내 내용은 1천 자 이내로 입력해 주세요.")
    if row["status"] == "disputed":
        note = required(note, "소관 이의에 대한 회신", 1000)
        status = "draft" if row["draft"] else "open"
    else:
        status = row["status"]
        note = note or row["note"]
    conn.execute(
        "UPDATE assignments SET status=?, due=?, note=?, version=version+1 WHERE id=?",
        (status, due, note, row["id"]),
    )
    action_name = "소관 이의 회신" if row["status"] == "disputed" else "내부 마감 변경"
    event(
        conn,
        actor,
        action_name,
        request["id"],
        row["id"],
        f"{due} {note}".strip(),
        notify="assignment",
        message=note or f"내부 마감이 {due}로 변경되었습니다.",
    )


def dispute_assignment(conn, actor, row, request, form):
    """부서가 소관이 아니라고 판단할 때 취합팀에 재배정을 요청한다."""
    check_version(row, form)
    check_state(row["status"] in WORKING_STATES)
    reason = required(form.get("note", ""), "소관 이의 사유", 1000)
    conn.execute(
        "UPDATE assignments SET status='disputed', version=version+1 WHERE id=?",
        (row["id"],),
    )
    event(
        conn,
        actor,
        "소관 이의",
        request["id"],
        row["id"],
        reason,
        notify="team",
        message=reason,
    )


def remind_pending(conn, actor, request, form):
    """마감이 지났거나 아직 제출하지 않은 부서에만 독촉 공지를 보낸다."""
    check_state(request["status"] == "collecting")
    targets = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM assignments WHERE request_id=? AND status IN ('open','draft','returned') ORDER BY id",
            (request["id"],),
        )
    ]
    if not targets:
        raise PortalError(400, "제출을 기다리는 부서가 없습니다.")
    body = (
        form.get("notice_body", "").strip()
        or "요구자료 제출 기한을 확인하고 답변을 제출해 주세요."
    )
    if len(body) > 2000:
        raise PortalError(400, "공지 내용은 2천 자 이내로 입력해 주세요.")
    event(
        conn,
        actor,
        "업무 공지",
        request["id"],
        None,
        f"미제출 부서 독촉 {len(targets)}곳",
        notify="assigned",
        message=body,
        targets=targets,
    )
    return len(targets)


def document_record(name, data, *, hancom_only=False):
    try:
        documents.validate_file(name, data, hancom_only=hancom_only)
    except documents.InvalidDocument as exc:
        raise PortalError(400, str(exc)) from exc
    except documents.DetectorUnavailable as exc:
        raise PortalError(503, str(exc)) from exc
    return name, hashlib.sha256(data).hexdigest(), data


def single_hancom_document(files):
    if len(files) > 1:
        raise PortalError(400, "이 항목에는 한글 문서 한 개를 첨부해 주세요.")
    if files:
        return document_record(*files[0], hancom_only=True)
    return None


def change_draft(conn, actor, row, form, files, action):
    check_version(row, form)
    check_state(row["status"] in ("open", "draft", "returned"))
    body = form.get("body", "").strip()
    if len(body) > 100000:
        raise PortalError(400, "답변은 10만 자 이내로 입력해 주세요.")
    na = form.get("na") == "1"
    try:
        meta = gongmun.parse_meta(
            form, submitting=(action == "submit"), parse_date=calendar_date
        )
    except gongmun.MetaError as exc:
        raise PortalError(400, str(exc)) from None
    for attachment_id in form.get("remove", []):
        conn.execute(
            "DELETE FROM attachments WHERE id=? AND assignment=? AND submission IS NULL",
            (attachment_id, row["id"]),
        )
    count, total_size = conn.execute(
        "SELECT count(*), coalesce(sum(length(content)),0) FROM attachments "
        "WHERE assignment=? AND submission IS NULL",
        (row["id"],),
    ).fetchone()
    if count + len(files) > MAX_FILES:
        raise PortalError(400, "제출본당 첨부는 최대 5개입니다.")
    if (
        total_size + sum(len(data) for _, data in files)
        > documents.MAX_SUBMISSION_FILES_SIZE
    ):
        raise PortalError(400, "제출본당 첨부 합계는 최대 40 MiB입니다.")
    for name, data in files:
        conn.execute(
            "INSERT INTO attachments(assignment, name, sha256, content) VALUES (?, ?, ?, ?)",
            (row["id"], *document_record(name, data)),
        )
    mine = questions.save_answers(conn, row["id"], form)
    status = "returned" if row["status"] == "returned" else "draft"
    if action == "submit":
        if na:
            required(body, "해당 없음 사유", 100000)
        elif not (body or count or files or mine):
            raise PortalError(400, "답변을 입력하거나 첨부를 추가해 주세요.")
        cursor = conn.execute(
            "INSERT INTO submissions(assignment, revision, body, na, actor, created, meta) "
            "SELECT ?, COALESCE(MAX(revision), 0)+1, ?, ?, ?, ?, ? FROM submissions "
            "WHERE assignment=?",
            (row["id"], body, na, actor, now(), dump_meta(meta), row["id"]),
        )
        conn.execute(
            "UPDATE attachments SET submission=? WHERE assignment=? AND submission IS NULL",
            (cursor.lastrowid, row["id"]),
        )
        questions.freeze_answers(
            conn, row["id"], cursor.lastrowid, mine, not_applicable=na
        )
        # 작성자·연락처는 보완 재제출에서 다시 쓰도록 초안 쪽에 남긴다.
        body, na, status = "", False, "submitted"
    conn.execute(
        "UPDATE assignments SET draft=?, na=?, status=?, draft_meta=?, version=version+1 WHERE id=?",
        (body, na, status, dump_meta(meta), row["id"]),
    )
    event(
        conn,
        actor,
        "부서 제출" if action == "submit" else "초안 저장",
        row["request_id"],
        row["id"],
        notify="team" if action == "submit" else None,
    )


def review(conn, actor, row, request, form, action):
    check_version(row, form)
    check_state(
        row["status"] == "submitted"
        or (action == "return" and row["status"] == "approved")
    )
    latest = conn.execute(
        "SELECT body, na, actor, meta FROM submissions WHERE assignment=? ORDER BY revision DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    if action == "approve" and latest["actor"] == actor:
        raise PortalError(
            403, "본인이 제출한 답변은 다른 취합 담당자가 승인해야 합니다."
        )
    note, due = "", row["due"]
    if action == "return":
        note = required(form.get("note", ""), "보완 사유", 3000)
        due = calendar_date(form.get("due", ""))
        if due > request["due"]:
            raise PortalError(400, "보완 마감일은 대외 마감일 이내여야 합니다.")
        reopen_for_revision(conn, row, note, due)
    else:
        conn.execute(
            "UPDATE assignments SET status='approved', version=version+1 WHERE id=?",
            (row["id"],),
        )
    event(
        conn,
        actor,
        "보완 요청" if action == "return" else "부서 승인",
        row["request_id"],
        row["id"],
        note,
        notify="assignment",
        message=note,
    )


def reopen_for_revision(conn, row, note, due=None):
    """최신 제출본(본문·메타·첨부·질의별 답변)을 초안으로 되돌리고 보완 요청 상태로 바꾼다."""
    latest = conn.execute(
        "SELECT body, na, meta FROM submissions WHERE assignment=? ORDER BY revision DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    if latest is not None:
        conn.execute(
            "UPDATE assignments SET draft=?, na=?, draft_meta=? WHERE id=?",
            (latest["body"], latest["na"], latest["meta"], row["id"]),
        )
        conn.execute(
            "INSERT INTO attachments(assignment, name, sha256, content) "
            "SELECT assignment, name, sha256, content FROM attachments WHERE submission=("
            "SELECT id FROM submissions WHERE assignment=? ORDER BY revision DESC LIMIT 1)",
            (row["id"],),
        )
        questions.restore_answers(conn, row["id"])
    conn.execute(
        "UPDATE assignments SET status='returned', note=?, due=?, version=version+1 WHERE id=?",
        (note, due or row["due"], row["id"]),
    )


def dump_meta(meta):
    return json.dumps(meta, ensure_ascii=False, sort_keys=True)


def load_meta(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        value = {}
    return {**gongmun.EMPTY_META, **(value if isinstance(value, dict) else {})}


def approved_sources(conn, request_id):
    return conn.execute(
        "SELECT s.*, d.name AS department_name FROM submissions s "
        "JOIN assignments a ON a.id=s.assignment JOIN scopes d ON d.id=a.department "
        "WHERE a.request_id=? AND a.status='approved' AND s.revision=("
        "SELECT max(revision) FROM submissions WHERE assignment=a.id) ORDER BY a.id",
        (request_id,),
    ).fetchall()


def collection_stamp(conn, request_id):
    snapshot = conn.execute(
        "SELECT id, version FROM assignments WHERE request_id=? ORDER BY id",
        (request_id,),
    )
    return token_hash(",".join(f"{row['id']}:{row['version']}" for row in snapshot))


def latest_final(conn, request_id):
    return conn.execute(
        "SELECT * FROM finals WHERE request_id=? ORDER BY revision DESC LIMIT 1",
        (request_id,),
    ).fetchone()


def change_request(conn, actor, row, form, action, files=()):
    check_version(row, form)
    request_id = row["id"]
    if action == "assign":
        check_state(row["status"] == "collecting")
        add_assignment(conn, actor, row, form)
    elif action == "questions":
        questions.add(conn, actor, row, form)
    elif action == "route":
        questions.route(conn, actor, row, form)
    elif action == "unquestion":
        questions.remove(conn, actor, row, form)
    elif action in ("prepare", "finalize"):
        check_state(row["status"] == "collecting")
        stamp = collection_stamp(conn, request_id)
        check_state(stamp == form.get("collection"))
        update_collection(
            conn, actor, row, form, files, stamp, finalize=(action == "finalize")
        )
    elif action == "sent":
        check_state(row["status"] == "finalized")
        sent_on = calendar_date(form.get("sent_on", ""))
        if sent_on > today():
            raise PortalError(400, "대외 제출일은 오늘까지의 날짜를 입력해 주세요.")
        final = latest_final(conn, request_id)
        if sent_on < final["created"][:10]:
            raise PortalError(400, "대외 제출일은 최종 확정일 이후여야 합니다.")
        conn.execute(
            "UPDATE finals SET sent_on=?, sent_by=? WHERE id=?",
            (sent_on, actor, final["id"]),
        )
        set_request_state(conn, row, "sent")
        event(
            conn, actor, "대외 제출 확인", request_id, detail=sent_on, notify="assigned"
        )
    elif action == "ledger":
        check_state(row["status"] == "sent")
        final = latest_final(conn, request_id)
        check_state(final["ledger_state"] == "unrecorded")
        ledger_id = required(form.get("ledger_id", ""), "대장 ID", 100)
        if row["ledger_id"] and row["ledger_id"] != ledger_id:
            raise PortalError(400, "요구 건에 연결된 대장 ID를 확인해 주세요.")
        conn.execute(
            "UPDATE requests SET ledger_id=? WHERE id=?", (ledger_id, request_id)
        )
        conn.execute(
            "UPDATE finals SET ledger_state='confirmed' WHERE id=?", (final["id"],)
        )
        set_request_state(conn, row, "sent")
        event(conn, actor, "대장 반영 수동 확인", request_id, detail=ledger_id)
    elif action == "reopen":
        check_state(row["status"] in ("finalized", "sent"))
        reason = required(form.get("note", ""), "재개 사유", 3000)
        set_request_state(conn, row, "collecting")
        event(conn, actor, "요구 건 재개", request_id, detail=reason, notify="assigned")


def set_request_state(conn, row, state):
    conn.execute(
        "UPDATE requests SET status=?, version=version+1 WHERE id=?", (state, row["id"])
    )


def update_collection(conn, actor, row, form, files, stamp, *, finalize):
    request_id = row["id"]
    document = single_hancom_document(files)
    if document:
        conn.execute(
            "INSERT INTO draft_documents VALUES (?, ?, ?, ?) ON CONFLICT(request_id) DO UPDATE SET "
            "name=excluded.name, sha256=excluded.sha256, content=excluded.content",
            (request_id, *document),
        )
    elif form.get("clear_document") == "1":
        conn.execute("DELETE FROM draft_documents WHERE request_id=?", (request_id,))
    native = conn.execute(
        "SELECT name FROM draft_documents WHERE request_id=?", (request_id,)
    ).fetchone()
    body = form.get("body", "").strip()
    if len(body) > 500000 or not (body or native):
        raise PortalError(
            400, "취합 답변을 50만 자 이내로 입력하거나 한글 문서를 첨부해 주세요."
        )
    if not finalize:
        conn.execute(
            "INSERT INTO collection_drafts VALUES (?, ?, ?, ?, ?) ON CONFLICT(request_id) DO UPDATE "
            "SET body=excluded.body, stamp=excluded.stamp, actor=excluded.actor, updated=excluded.updated",
            (request_id, body, stamp, actor, now()),
        )
        set_request_state(conn, row, "collecting")
        event(conn, actor, "취합 초안 저장", request_id)
        return
    sources = approved_sources(conn, request_id)
    missing = conn.execute(
        "SELECT d.name FROM assignments a JOIN scopes d ON d.id=a.department "
        "WHERE request_id=? AND status NOT IN ('approved','cancelled') ORDER BY a.id",
        (request_id,),
    ).fetchall()
    check_state(bool(sources or missing))
    if (
        any(gongmun.restricted(load_meta(item["meta"])) for item in sources)
        and form.get("privacy_ack") != "1"
    ):
        raise PortalError(
            400,
            "비공개·부분공개 또는 개인정보가 포함된 제출본이 있습니다. "
            "공개 범위와 가림 처리를 확인했다고 표시한 뒤 확정해 주세요.",
        )
    exception = ""
    open_questions = questions.unanswered(conn, request_id, sources)
    if missing or open_questions:
        reason = required(
            form.get("exception", ""), "미승인 부서·미답변 질의 예외 사유", 3000
        )
        gaps = [item["name"] for item in missing]
        if open_questions:
            gaps.append("미답변 질의 " + ", ".join(map(str, open_questions)))
        exception = ", ".join(gaps) + " / " + reason
    previous = latest_final(conn, request_id)
    revision = previous["revision"] + 1 if previous else 1
    final_id = conn.execute(
        "INSERT INTO finals(request_id, revision, body, exception, actor, created) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (request_id, revision, body, exception, actor, now()),
    ).lastrowid
    conn.executemany(
        "INSERT INTO final_sources VALUES (?, ?)",
        [(final_id, item["id"]) for item in sources],
    )
    conn.execute(
        "INSERT INTO final_documents SELECT ?, name, sha256, content FROM draft_documents WHERE request_id=?",
        (final_id, request_id),
    )
    conn.execute("DELETE FROM draft_documents WHERE request_id=?", (request_id,))
    conn.execute("DELETE FROM collection_drafts WHERE request_id=?", (request_id,))
    set_request_state(conn, row, "finalized")
    restricted = [
        item["department_name"]
        for item in sources
        if gongmun.restricted(load_meta(item["meta"]))
    ]
    detail = f"{revision}판 {exception}".strip()
    if restricted:
        detail += " / 공개 범위 확인: " + ", ".join(restricted)
    event(conn, actor, "최종 확정", request_id, detail=detail, notify="assigned")
