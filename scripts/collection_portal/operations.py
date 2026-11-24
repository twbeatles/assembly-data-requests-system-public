"""Read-only inspection, audit export and verified SQLite snapshots."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import mkstemp
from time import time

from . import auth, store


def open_portal(path):
    return closing(
        sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10
        )
    )


def schema_version(conn):
    kind = conn.execute("PRAGMA application_id").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if kind != store.APPLICATION_ID or not 1 <= version <= store.SCHEMA_VERSION:
        raise store.PortalError(400, "지원되는 포털 DB가 아닙니다.")
    return version


def verify(conn):
    version = schema_version(conn)
    if conn.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",):
        raise store.PortalError(400, "포털 DB 무결성 검사에 실패했습니다.")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise store.PortalError(400, "포털 DB 참조 무결성 검사에 실패했습니다.")
    if version >= 5:
        verify_chain(conn)
    return version


def verify_chain(conn):
    """Recompute the audit hash chain. Returns (last id, last chain) or raises on any break."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    previous, last_id = store.CHAIN_GENESIS, 0
    for row in cursor.execute("SELECT * FROM events ORDER BY id"):
        expected = store.chain_value(previous, row)
        if row["chain"] != expected:
            raise store.PortalError(
                400,
                f"감사 이력 {row['id']}번에서 변경 또는 삭제 흔적이 발견되었습니다.",
            )
        previous, last_id = expected, row["id"]
    return last_id, previous


def audit_head(conn):
    if schema_version(conn) < 5:
        raise store.PortalError(
            400, "감사 체인을 쓰려면 포털 DB를 5 버전으로 업데이트하세요."
        )
    last_id, chain = verify_chain(conn)
    return json.dumps({"last_event_id": last_id, "chain": chain}, ensure_ascii=True)


def snapshot(source, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise store.PortalError(409, "백업 저장 위치에 이미 파일이 있습니다.")
    with open_portal(source) as live:
        schema_version(live)
        descriptor, temp_name = mkstemp(
            prefix=".collection-backup-", dir=destination.parent
        )
        os.close(descriptor)
        temp = Path(temp_name)
        try:
            with closing(sqlite3.connect(temp)) as output:
                live.backup(output)
                verify(output)
            # Publish only a verified copy. A competing backup cannot replace an existing file.
            os.link(temp, destination)
        finally:
            temp.unlink()


def audit_lines(conn, after, limit):
    if after < 0 or not 1 <= limit <= 1000:
        raise store.PortalError(
            400, "이력 시작 번호와 출력 개수(1~1000)를 확인해 주세요."
        )
    conn.row_factory = sqlite3.Row
    chain = ", chain" if schema_version(conn) >= 5 else ""
    for row in conn.execute(
        "SELECT id, request_id, assignment, actor, action, detail, created"
        + chain
        + " "
        "FROM events WHERE id>? ORDER BY id LIMIT ?",
        (after, limit),
    ):
        yield json.dumps(dict(row), ensure_ascii=True)


def integration_inventory(conn):
    conn.row_factory = sqlite3.Row
    if schema_version(conn) < 3:
        raise store.PortalError(
            400, "연동 목록을 조회하려면 포털 DB를 3 버전으로 업데이트하세요."
        )
    for row in conn.execute(
        "SELECT c.id AS integration_id, c.team, c.can_intake, c.active, k.id AS key_id, "
        "k.issued, k.expires, k.revoked FROM integration_clients c "
        "LEFT JOIN integration_keys k ON k.client=c.id ORDER BY c.id, k.issued, k.id"
    ):
        yield json.dumps(dict(row), ensure_ascii=True)


def account_access(conn, login):
    schema_version(conn)
    conn.row_factory = sqlite3.Row
    account = store.require_row(
        conn.execute("SELECT enabled FROM accounts WHERE login=?", (login,)).fetchone()
    )
    grants = store.visible_grants(conn, login)
    result = {
        "login": login,
        "enabled": bool(account["enabled"]),
        "effective_grants": [dict(row) for row in grants],
    }
    if schema_version(conn) >= 5:
        result.update(auth.account_status(conn, login))
    return json.dumps(result, ensure_ascii=True)


def session_lines(conn, login):
    conn.row_factory = sqlite3.Row
    if schema_version(conn) < 5:
        raise store.PortalError(
            400, "세션 목록을 보려면 포털 DB를 5 버전으로 업데이트하세요."
        )
    for row in auth.list_sessions(conn, login):
        yield json.dumps(dict(row), ensure_ascii=True)


def identity_lines(conn):
    conn.row_factory = sqlite3.Row
    if schema_version(conn) < 5:
        raise store.PortalError(
            400, "SSO 연결 목록을 보려면 포털 DB를 5 버전으로 업데이트하세요."
        )
    for row in conn.execute(
        "SELECT login, issuer, subject, created FROM identities ORDER BY login, issuer"
    ):
        yield json.dumps(dict(row), ensure_ascii=True)


def invalidate_access(conn):
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM sso_states")
    conn.execute(
        "UPDATE integration_keys SET revoked=? WHERE revoked IS NULL", (int(time()),)
    )
    store.event(conn, "포털 콘솔", "전체 세션 및 연동 키 폐기")
