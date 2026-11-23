"""Partner intake and status API. Machine identities have their own authorization namespace."""

import hashlib
import json
import re
import secrets
from time import time

from . import store

PREFIX = "/integrations/v1/"
REQUESTS = PREFIX + "requests"
EXTERNAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
ITEM = re.compile(r"/integrations/v1/requests/([A-Za-z0-9][A-Za-z0-9_.-]{0,79})")
BEARER = re.compile(r"(?i:Bearer) (cpi_[A-Za-z0-9_-]{43})")
MAX_JSON = 256 * 1024
INTAKE_FIELDS = {"external_id", "title", "requester", "instructions", "due"}
CONSOLE_ACTOR = "포털 콘솔"


def actor_name(client):
    return f"integration:{client}"


def configure_client(conn, client, team, can_intake):
    store.require_row(
        conn.execute(
            "SELECT 1 FROM scopes WHERE id=? AND kind='team'", (team,)
        ).fetchone()
    )
    row = conn.execute(
        "SELECT team, active FROM integration_clients WHERE id=?", (client,)
    ).fetchone()
    if row and (row["team"] != team or not row["active"]):
        raise store.PortalError(
            409, "다른 팀에 연결하거나 폐기된 연동을 다시 쓰려면 새 연동 ID를 만드세요."
        )
    conn.execute(
        "INSERT INTO integration_clients(id, team, can_intake) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET can_intake=excluded.can_intake",
        (client, team, can_intake),
    )
    scope = "intake+status" if can_intake else "status"
    store.event(
        conn, CONSOLE_ACTOR, "연동 권한 설정", detail=f"{client} / {team} / {scope}"
    )


def issue_key(conn, client, days):
    store.require_row(
        conn.execute(
            "SELECT 1 FROM integration_clients WHERE id=? AND active=1", (client,)
        ).fetchone()
    )
    if not 1 <= days <= 90:
        raise store.PortalError(400, "연동 키 유효기간은 1~90일입니다.")
    key_id = secrets.token_hex(12)
    token = "cpi_" + secrets.token_urlsafe(32)
    issued = int(time())
    expires = issued + days * 86400
    conn.execute(
        "INSERT INTO integration_keys VALUES (?, ?, ?, ?, ?, NULL)",
        (key_id, client, store.token_hash(token), issued, expires),
    )
    store.event(conn, CONSOLE_ACTOR, "연동 키 발급", detail=f"{client} / {key_id}")
    return key_id, token, expires


def revoke_key(conn, client, key_id):
    row = store.require_row(
        conn.execute(
            "SELECT revoked FROM integration_keys WHERE client=? AND id=?",
            (client, key_id),
        ).fetchone()
    )
    if row["revoked"] is None:
        conn.execute(
            "UPDATE integration_keys SET revoked=? WHERE client=? AND id=?",
            (int(time()), client, key_id),
        )
        store.event(conn, CONSOLE_ACTOR, "연동 키 폐기", detail=f"{client} / {key_id}")


def disable_client(conn, client):
    store.require_row(
        conn.execute(
            "SELECT 1 FROM integration_clients WHERE id=?", (client,)
        ).fetchone()
    )
    conn.execute("UPDATE integration_clients SET active=0 WHERE id=?", (client,))
    conn.execute(
        "UPDATE integration_keys SET revoked=? WHERE client=? AND revoked IS NULL",
        (int(time()), client),
    )
    store.event(conn, CONSOLE_ACTOR, "연동 중지", detail=client)


def authenticate(conn, authorization):
    match = BEARER.fullmatch(authorization)
    if match:
        client = conn.execute(
            "SELECT c.id, c.team, c.can_intake FROM integration_keys k JOIN integration_clients c "
            "ON c.id=k.client WHERE k.token_hash=? AND k.expires>? AND k.revoked IS NULL AND c.active=1",
            (store.token_hash(match[1]), int(time())),
        ).fetchone()
        if client:
            return client
    raise store.PortalError(401, "유효한 연동 키가 필요합니다.")


def parse_payload(raw):
    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError
            result[name] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if (
            not isinstance(payload, dict)
            or payload.keys() != INTAKE_FIELDS
            or any(not isinstance(value, str) for value in payload.values())
        ):
            raise ValueError
        if any("\x00" in value for value in payload.values()):
            raise ValueError
        # JSON escape sequences can produce lone surrogates despite UTF-8 input.
        for value in payload.values():
            value.encode("utf-8")
    except (ValueError, RecursionError):
        raise store.PortalError(400, "요청 JSON 필드와 값을 확인해 주세요.") from None
    data = {name: value.strip() for name, value in payload.items()}
    if not EXTERNAL_ID.fullmatch(data["external_id"]):
        raise store.PortalError(400, "외부 ID는 영문·숫자로 시작하며 최대 80자입니다.")
    return data


def intake(conn, client, data):
    if not client["can_intake"]:
        raise store.PortalError(403, "이 연동에는 접수 권한이 없습니다.")
    external_id = data["external_id"]
    fingerprint = hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("ascii")
    ).hexdigest()
    existing = conn.execute(
        "SELECT fingerprint FROM integration_requests WHERE client=? AND external_id=?",
        (client["id"], external_id),
    ).fetchone()
    if existing:
        if existing["fingerprint"] != fingerprint:
            raise store.PortalError(409, "같은 외부 ID로 접수한 내용이 다릅니다.")
        return 200, status(conn, client, external_id)
    request_id = store.insert_request(
        conn, actor_name(client["id"]), client["team"], data
    )
    conn.execute(
        "INSERT INTO integration_requests VALUES (?, ?, ?, ?)",
        (client["id"], external_id, request_id, fingerprint),
    )
    return 201, status(conn, client, external_id)


def status(conn, client, external_id):
    row = store.require_row(
        conn.execute(
            "SELECT r.id, r.status, r.due FROM integration_requests i JOIN requests r ON r.id=i.request_id "
            "WHERE i.client=? AND i.external_id=? AND r.team=?",
            (client["id"], external_id, client["team"]),
        ).fetchone()
    )
    count = conn.execute(
        "SELECT count(*) FROM assignments WHERE request_id=?", (row["id"],)
    ).fetchone()[0]
    state = (
        "received" if row["status"] == "collecting" and count == 0 else row["status"]
    )
    return {"external_id": external_id, "status": state, "due": row["due"]}
