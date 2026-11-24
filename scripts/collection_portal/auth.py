"""Account lockout, TOTP second factor and session lifecycle for the portal."""

import base64
import hashlib
import hmac
import secrets
import struct
from time import time
from urllib.parse import quote

from . import store

MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
SESSION_SECONDS = 8 * 3600
IDLE_SECONDS = 30 * 60
TOUCH_SECONDS = 60
TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
ISSUER_LABEL = "요구자료 취합"
CONSOLE_ACTOR = "포털 콘솔"

# Generic outcome texts. A failure never discloses whether the account exists, is locked or uses MFA.
FAILED = "계정, 비밀번호 또는 인증 코드를 확인해 주세요."
MFA_REQUIRED = (
    "이 포털은 인증 앱 등록이 필요합니다. 운영 담당자에게 등록을 요청해 주세요."
)


# --- TOTP (RFC 6238, HMAC-SHA1, 6 digits, 30 s) -------------------------------------------------


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _secret_bytes(secret):
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded, casefold=True)


def totp_code(secret, step):
    digest = hmac.new(
        _secret_bytes(secret), struct.pack(">Q", step), hashlib.sha1
    ).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def match_totp(secret, code, last_step, at=None):
    """Return the accepted time step, or None. Steps at or before `last_step` are replays."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = int((time() if at is None else at) // TOTP_PERIOD)
    accepted = None
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        # Compare every candidate so timing does not reveal which window matched.
        if hmac.compare_digest(totp_code(secret, step), code) and step > last_step:
            accepted = step
    return accepted


def provisioning_uri(login, secret):
    label = quote(f"{ISSUER_LABEL}:{login}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER_LABEL, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"
    )


def enroll_totp(conn, login, secret, code):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    step = match_totp(secret, code, 0)
    if step is None:
        raise store.PortalError(
            400, "인증 앱의 현재 코드가 일치하지 않습니다. 시각 동기화를 확인해 주세요."
        )
    conn.execute(
        "UPDATE accounts SET totp_secret=?, totp_step=? WHERE login=?",
        (secret, step, login),
    )
    end_sessions(conn, login)
    store.event(conn, CONSOLE_ACTOR, "2차 인증 등록", detail=login)


def reset_totp(conn, login):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    conn.execute(
        "UPDATE accounts SET totp_secret=NULL, totp_step=0 WHERE login=?", (login,)
    )
    end_sessions(conn, login)
    store.event(conn, CONSOLE_ACTOR, "2차 인증 초기화", detail=login)


# --- password login with persistent lockout -----------------------------------------------------


def password_login(conn, login, password, code, *, require_mfa=False):
    """Return (login, None) on success or (None, message). Always commits failure counters."""
    clock = int(time())
    row = conn.execute("SELECT * FROM accounts WHERE login=?", (login,)).fetchone()
    if row is None:
        store.password_hash(password, bytes(16))
        return None, FAILED
    salt = bytes.fromhex(row["password"].split(":")[0])
    password_ok = hmac.compare_digest(
        row["password"], store.password_hash(password, salt)
    )
    if not row["enabled"]:
        return None, FAILED
    if row["locked_until"] > clock:
        store.event(conn, login, "로그인 거부(잠금 중)")
        return None, FAILED
    step = None
    if password_ok and row["totp_secret"]:
        step = match_totp(row["totp_secret"], code, row["totp_step"], clock)
    if not password_ok or (row["totp_secret"] and step is None):
        failures = row["failed_logins"] + 1
        if failures >= MAX_FAILURES:
            conn.execute(
                "UPDATE accounts SET failed_logins=0, locked_until=? WHERE login=?",
                (clock + LOCK_SECONDS, login),
            )
            end_sessions(conn, login)
            store.event(conn, login, "계정 잠금", detail=f"연속 실패 {failures}회")
        else:
            conn.execute(
                "UPDATE accounts SET failed_logins=? WHERE login=?", (failures, login)
            )
            store.event(
                conn,
                login,
                "로그인 실패",
                detail="인증 코드" if password_ok else "비밀번호",
            )
        return None, FAILED
    if require_mfa and not row["totp_secret"]:
        store.event(conn, login, "로그인 거부(2차 인증 미등록)")
        return None, MFA_REQUIRED
    conn.execute(
        "UPDATE accounts SET failed_logins=0, locked_until=0, totp_step=? WHERE login=?",
        (step if step is not None else row["totp_step"], login),
    )
    return login, None


def unlock(conn, login):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    conn.execute(
        "UPDATE accounts SET failed_logins=0, locked_until=0 WHERE login=?", (login,)
    )
    store.event(conn, CONSOLE_ACTOR, "계정 잠금 해제", detail=login)


# --- sessions -----------------------------------------------------------------------------------


def create_session(conn, login, method, replaced_token=""):
    clock = int(time())
    conn.execute(
        "DELETE FROM sessions WHERE expiry<=? OR token=?",
        (clock, store.token_hash(replaced_token)),
    )
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token, login, expiry, created, last_seen, method) VALUES (?, ?, ?, ?, ?, ?)",
        (store.token_hash(token), login, clock + SESSION_SECONDS, clock, clock, method),
    )
    store.event(conn, login, "로그인" if method == "password" else "SSO 로그인")
    return token


def session_row(conn, token, idle=IDLE_SECONDS):
    if not token:
        return None
    clock = int(time())
    return conn.execute(
        "SELECT s.login, s.last_seen FROM sessions s JOIN accounts a ON a.login=s.login "
        "WHERE s.token=? AND s.expiry>? AND s.last_seen>? AND a.enabled=1",
        (store.token_hash(token), clock, clock - idle),
    ).fetchone()


def touch_session(conn, token):
    clock = int(time())
    conn.execute(
        "UPDATE sessions SET last_seen=? WHERE token=? AND last_seen<?",
        (clock, store.token_hash(token), clock - TOUCH_SECONDS),
    )


def end_sessions(conn, login):
    return conn.execute("DELETE FROM sessions WHERE login=?", (login,)).rowcount


def list_sessions(conn, login):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    return conn.execute(
        "SELECT created, last_seen, expiry, method FROM sessions WHERE login=? ORDER BY created",
        (login,),
    ).fetchall()


def account_status(conn, login):
    row = store.require_row(
        conn.execute(
            "SELECT enabled, locked_until, totp_secret IS NOT NULL AS mfa FROM accounts WHERE login=?",
            (login,),
        ).fetchone()
    )
    identities = conn.execute(
        "SELECT issuer, subject FROM identities WHERE login=? ORDER BY issuer, subject",
        (login,),
    ).fetchall()
    return {
        "locked": row["locked_until"] > int(time()),
        "mfa": bool(row["mfa"]),
        "sso_identities": [dict(item) for item in identities],
    }
