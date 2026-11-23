"""Start the portal or administer its local pilot accounts."""

import argparse
import getpass
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import integrations, operations, store
from .http import PortalServer


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
        raise argparse.ArgumentTypeError(
            "ID는 1~80자의 영문·숫자·점·밑줄·하이픈을 사용합니다."
        )
    return value


def https_origin(value):
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.port != 443
            and not (parsed.path or parsed.query or parsed.fragment)
            and parsed.netloc == parsed.netloc.lower()
            and value == f"https://{parsed.netloc}"
            and not any(c.isspace() for c in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("예: https://collection.example.org")
    return value


def parser():
    cli = argparse.ArgumentParser(description="부서별 요구자료 취합 포털")
    cli.add_argument(
        "--db", type=Path, required=True, help="별도 포털 SQLite 파일 경로"
    )
    sub = cli.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8090)
    serve.add_argument("--https-origin", type=https_origin)
    flags = serve.add_mutually_exclusive_group()
    flags.add_argument("--enable-integrations", action="store_true")
    flags.add_argument("--local-test-integrations", action="store_true")
    scope = sub.add_parser("scope")
    scope.add_argument("kind", choices=("department", "team"))
    scope.add_argument("id", type=identifier)
    scope.add_argument("name")
    sub.add_parser("demo")
    sub.add_parser("account").add_argument("login", type=identifier)
    sub.add_parser("disable").add_argument("login", type=identifier)
    sub.add_parser("access").add_argument("login", type=identifier)
    configure = sub.add_parser("integration-config")
    configure.add_argument("client", type=identifier)
    configure.add_argument("team", type=identifier)
    configure.add_argument("--intake", action="store_true")
    issue = sub.add_parser("integration-key")
    issue.add_argument("client", type=identifier)
    issue.add_argument("--days", type=int, default=30)
    revoke = sub.add_parser("integration-revoke-key")
    revoke.add_argument("client", type=identifier)
    revoke.add_argument("key_id", type=identifier)
    sub.add_parser("integration-disable").add_argument("client", type=identifier)
    sub.add_parser("integration-list")
    sub.add_parser("invalidate-access")
    sub.add_parser("check")
    sub.add_parser("backup").add_argument("destination", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--after", type=int, default=0)
    audit.add_argument("--limit", type=int, default=100)
    for action in ("grant", "revoke"):
        permission = sub.add_parser(action)
        permission.add_argument("login", type=identifier)
        permission.add_argument("scope", type=identifier)
        permission.add_argument("role", choices=store.ROLES)
    return cli


def set_account(conn, login, password):
    conn.execute(
        "INSERT INTO accounts(login, password) VALUES (?, ?) ON CONFLICT(login) DO UPDATE "
        "SET password=excluded.password, enabled=1",
        (login, store.password_hash(password)),
    )
    conn.execute("DELETE FROM sessions WHERE login=?", (login,))
    store.event(conn, "포털 콘솔", "계정 설정", detail=login)


def set_grant(conn, login, scope, role, grant):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    row = store.require_row(
        conn.execute("SELECT kind FROM scopes WHERE id=?", (scope,)).fetchone()
    )
    if (role == "department") != (row["kind"] == "department"):
        raise store.PortalError(400, "부서와 취합팀에 해당하는 역할을 지정해 주세요.")
    if grant:
        changed = conn.execute(
            "INSERT OR IGNORE INTO grants VALUES (?, ?, ?)", (login, scope, role)
        )
    else:
        changed = conn.execute(
            "DELETE FROM grants WHERE login=? AND scope=? AND role=?",
            (login, scope, role),
        )
    if changed.rowcount:
        store.event(
            conn,
            "포털 콘솔",
            "권한 부여" if grant else "권한 회수",
            detail=f"{login} / {scope} / {role}",
        )


def demo(conn):
    if conn.execute(
        "SELECT 1 FROM scopes UNION ALL SELECT 1 FROM accounts LIMIT 1"
    ).fetchone():
        raise store.PortalError(409, "시범 계정은 새 DB에서만 만들 수 있습니다.")
    conn.executemany(
        "INSERT INTO scopes VALUES (?, ?, ?)",
        [
            ("collect", "취합관리팀", "team"),
            ("planning", "기획부서", "department"),
            ("operations", "운영부서", "department"),
        ],
    )
    credentials = []
    for login, scope, role in (
        ("lead", "collect", "lead"),
        ("reviewer", "collect", "collector"),
        ("planning", "planning", "department"),
        ("operations", "operations", "department"),
    ):
        password = secrets.token_urlsafe(18)
        set_account(conn, login, password)
        set_grant(conn, login, scope, role, True)
        credentials.append((login, password))
    due = (datetime.now(store.KST).date() + timedelta(days=7)).isoformat()
    request_id = store.create_request(
        conn,
        "lead",
        {
            "team": "collect",
            "title": "부서별 업무 추진 현황 제출",
            "requester": "시범 요구자",
            "due": due,
            "instructions": "주요 사업의 추진 현황과 향후 일정을 작성해 주세요.",
        },
    )
    for scope in ("planning", "operations"):
        request, _, _ = store.request_for(conn, "lead", request_id)
        store.add_assignment(conn, "lead", request, {"department": scope, "due": due})
    return credentials


def main():
    cli = parser()
    args = cli.parse_args()
    args.db = args.db.resolve()
    credentials = []
    password = ""
    try:
        if args.command in ("backup", "check", "audit", "integration-list", "access"):
            run_inspection(args)
            return
        if args.command == "serve":
            if args.enable_integrations and not args.https_origin:
                cli.error("--enable-integrations에는 --https-origin이 필요합니다.")
            if args.local_test_integrations and args.https_origin:
                cli.error(
                    "--local-test-integrations는 루프백 HTTP 시험에서만 사용합니다."
                )
        store.initialize(args.db)
        if args.command == "serve":
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
            with PortalServer(
                ("127.0.0.1", args.port),
                args.db,
                args.https_origin,
                enable_integrations=args.enable_integrations
                or args.local_test_integrations,
            ) as server:
                print(f"요구자료 취합 포털  {server.origin}", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            return
        if args.command == "account":
            password = getpass.getpass("비밀번호 (12~256자) ")
            if not 12 <= len(password) <= 256 or password != getpass.getpass(
                "비밀번호 확인 "
            ):
                cli.error("비밀번호 길이와 확인값을 확인해 주세요.")
        with store.connect(args.db, write=True) as conn:
            if args.command == "demo":
                credentials = demo(conn)
            elif args.command == "scope":
                name = store.required(args.name, "표시 이름", 120)
                existing = conn.execute(
                    "SELECT kind FROM scopes WHERE id=?", (args.id,)
                ).fetchone()
                if existing and existing["kind"] != args.kind:
                    raise store.PortalError(
                        409, "이미 등록한 범위의 종류는 바꿀 수 없습니다."
                    )
                conn.execute(
                    "INSERT INTO scopes VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE "
                    "SET name=excluded.name",
                    (args.id, name, args.kind),
                )
                store.event(conn, "포털 콘솔", "조직 설정", detail=args.id)
            elif args.command == "account":
                set_account(conn, args.login, password)
            elif args.command == "disable":
                store.require_row(
                    conn.execute(
                        "SELECT 1 FROM accounts WHERE login=?", (args.login,)
                    ).fetchone()
                )
                conn.execute(
                    "UPDATE accounts SET enabled=0 WHERE login=?", (args.login,)
                )
                conn.execute("DELETE FROM sessions WHERE login=?", (args.login,))
                store.event(conn, "포털 콘솔", "계정 사용 중지", detail=args.login)
            elif args.command in ("grant", "revoke"):
                set_grant(
                    conn, args.login, args.scope, args.role, args.command == "grant"
                )
            elif args.command == "integration-config":
                integrations.configure_client(conn, args.client, args.team, args.intake)
            elif args.command == "integration-key":
                key_id, token, expires = integrations.issue_key(
                    conn, args.client, args.days
                )
                credentials = [
                    ("key_id", key_id),
                    (
                        "expires",
                        datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                    ),
                    ("token", token),
                ]
            elif args.command == "integration-revoke-key":
                integrations.revoke_key(conn, args.client, args.key_id)
            elif args.command == "integration-disable":
                integrations.disable_client(conn, args.client)
            elif args.command == "invalidate-access":
                operations.invalidate_access(conn)
    except (store.PortalError, sqlite3.Error, OSError) as exc:
        cli.error(str(exc))
    for account, password in credentials:
        print(f"{account}: {password}")


def run_inspection(args):
    if args.command == "backup":
        operations.snapshot(args.db, args.destination)
        print("백업 저장 및 무결성 검사 완료")
        return
    with operations.open_portal(args.db) as conn:
        if args.command == "check":
            print(f"포털 DB 스키마 {operations.verify(conn)} · 무결성 검사 통과")
        elif args.command == "audit":
            for line in operations.audit_lines(conn, args.after, args.limit):
                print(line)
        elif args.command == "integration-list":
            for line in operations.integration_inventory(conn):
                print(line)
        elif args.command == "access":
            print(operations.account_access(conn, args.login))


if __name__ == "__main__":
    main()
