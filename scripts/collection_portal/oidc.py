"""OpenID Connect sign-in (authorization code flow with PKCE) without third-party dependencies.

ID tokens are accepted only as RS256 JWS. Signature verification builds the complete expected
EMSA-PKCS1-v1_5 block (RFC 8017 §9.2) and compares it in constant time, so no ASN.1 parsing of
attacker-controlled data takes place. Accounts are found by the IdP's stable `iss` + `sub` pair
only; e-mail addresses and display names never grant access.
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic, time
from typing import Callable, Optional
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote, urlencode, urlsplit

CLOCK_SKEW = 60
STATE_SECONDS = 600
MAX_RESPONSE = 1024 * 1024
FETCH_TIMEOUT = 10
JWKS_REFRESH_SECONDS = 60
MIN_RSA_BITS = 2048
# DER prefix of DigestInfo for SHA-256 (RFC 8017 §9.2 note 1).
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class OidcError(Exception):
    """The sign-in cannot be trusted. The message is for the server log, not the browser."""


def secure_url(value, label):
    try:
        parts = urlsplit(value)
    except (TypeError, ValueError):
        raise OidcError(f"{label} 주소가 올바르지 않습니다.") from None
    loopback_http = parts.scheme == "http" and parts.hostname in LOOPBACK
    if (
        not (parts.scheme == "https" or loopback_http)
        or not parts.hostname
        or parts.username
        or parts.fragment
    ):
        raise OidcError(f"{label}은(는) HTTPS 주소여야 합니다.")
    return value


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OidcError("IdP 응답이 다른 주소로 이동을 요구했습니다.")


_OPENER = urlrequest.build_opener(_NoRedirect)


def fetch_json(url, data=None, headers=None):
    req = urlrequest.Request(
        url, data=data, headers={"Accept": "application/json", **(headers or {})}
    )
    try:
        with _OPENER.open(req, timeout=FETCH_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE + 1)
    except urlerror.HTTPError as exc:
        raise OidcError(f"IdP가 HTTP {exc.code}로 응답했습니다.") from None
    except (urlerror.URLError, OSError) as exc:
        raise OidcError(f"IdP에 연결하지 못했습니다: {type(exc).__name__}") from None
    if len(body) > MAX_RESPONSE:
        raise OidcError("IdP 응답이 너무 큽니다.")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise OidcError("IdP 응답이 JSON이 아닙니다.") from None
    if not isinstance(value, dict):
        raise OidcError("IdP 응답 형식이 올바르지 않습니다.")
    return value


def b64url_decode(text):
    if (
        not isinstance(text, str)
        or not text
        or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for c in text
        )
    ):
        raise OidcError("base64url 값이 올바르지 않습니다.")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise OidcError("base64url 값이 올바르지 않습니다.") from None


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def rsa_public_key(jwk):
    if (
        jwk.get("kty") != "RSA"
        or jwk.get("use", "sig") != "sig"
        or jwk.get("alg", "RS256") != "RS256"
    ):
        return None
    n = int.from_bytes(b64url_decode(jwk.get("n", "")), "big")
    e = int.from_bytes(b64url_decode(jwk.get("e", "")), "big")
    if n.bit_length() < MIN_RSA_BITS or e < 3 or e % 2 == 0:
        return None
    return n, e


def rs256_verify(key, signing_input, signature):
    n, e = key
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= n:
        return False
    encoded = pow(value, e, n).to_bytes(size, "big")
    digest = SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    padding = size - len(digest) - 3
    if padding < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest
    return hmac.compare_digest(encoded, expected)


def _json_part(segment, label):
    try:
        value = json.loads(b64url_decode(segment))
    except (ValueError, UnicodeError):
        raise OidcError(f"ID 토큰 {label}이(가) JSON이 아닙니다.") from None
    if not isinstance(value, dict):
        raise OidcError(f"ID 토큰 {label} 형식이 올바르지 않습니다.")
    return value


def _number(claims, name) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OidcError(f"ID 토큰 {name}가 숫자가 아닙니다.")
    return value


@dataclass
class OidcProvider:
    issuer: str
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    scopes: str = "openid"
    required_amr: tuple = ()
    label: str = "기관 계정"
    token_auth: str = "client_secret_basic"
    fetch: Callable[..., dict] = fetch_json
    _keys: dict = field(default_factory=dict)
    _keys_loaded: float = -1e9
    _lock: Lock = field(default_factory=Lock)

    @classmethod
    def from_config(cls, config, environ, fetch=fetch_json):
        if not isinstance(config, dict):
            raise OidcError("OIDC 설정은 JSON 객체여야 합니다.")
        unknown = set(config) - {
            "issuer",
            "client_id",
            "client_secret_env",
            "scopes",
            "required_amr",
            "label",
            "token_auth",
        }
        if unknown:
            raise OidcError("알 수 없는 OIDC 설정 항목: " + ", ".join(sorted(unknown)))
        issuer = secure_url(str(config.get("issuer", "")), "issuer")
        client_id = str(config.get("client_id", ""))
        secret_env = str(config.get("client_secret_env", ""))
        secret = environ.get(secret_env, "") if secret_env else ""
        if not client_id or not secret:
            raise OidcError(
                "client_id와 client_secret_env(환경 변수에 값이 있어야 함)를 설정해 주세요."
            )
        scopes = str(config.get("scopes", "openid"))
        if "openid" not in scopes.split():
            raise OidcError("scopes에 openid가 있어야 합니다.")
        amr = config.get("required_amr", [])
        if not isinstance(amr, list) or not all(isinstance(item, str) for item in amr):
            raise OidcError("required_amr는 문자열 목록이어야 합니다.")
        token_auth = config.get("token_auth", "client_secret_basic")
        if token_auth not in ("client_secret_basic", "client_secret_post"):
            raise OidcError(
                "token_auth는 client_secret_basic 또는 client_secret_post입니다."
            )
        discovery = fetch(issuer.rstrip("/") + "/.well-known/openid-configuration")
        if discovery.get("issuer") != issuer:
            raise OidcError("discovery 문서의 issuer가 설정과 다릅니다.")
        algs = discovery.get("id_token_signing_alg_values_supported", ["RS256"])
        if not isinstance(algs, list) or "RS256" not in algs:
            raise OidcError("IdP가 RS256 ID 토큰 서명을 지원하지 않습니다.")
        return cls(
            issuer=issuer,
            client_id=client_id,
            client_secret=secret,
            authorization_endpoint=secure_url(
                str(discovery.get("authorization_endpoint", "")),
                "authorization_endpoint",
            ),
            token_endpoint=secure_url(
                str(discovery.get("token_endpoint", "")), "token_endpoint"
            ),
            jwks_uri=secure_url(str(discovery.get("jwks_uri", "")), "jwks_uri"),
            scopes=scopes,
            required_amr=tuple(amr),
            label=str(config.get("label", "기관 계정"))[:40],
            token_auth=token_auth,
            fetch=fetch,
        )

    def authorization_url(self, redirect_uri, state, nonce, challenge):
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": self.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if urlsplit(self.authorization_endpoint).query else "?"
        return self.authorization_endpoint + separator + query

    def exchange(self, code, verifier, redirect_uri):
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self.token_auth == "client_secret_basic":
            pair = (
                f"{quote(self.client_id, safe='')}:{quote(self.client_secret, safe='')}"
            )
            headers["Authorization"] = "Basic " + base64.b64encode(
                pair.encode()
            ).decode("ascii")
        else:
            form.update(client_id=self.client_id, client_secret=self.client_secret)
        response = self.fetch(
            self.token_endpoint, urlencode(form).encode("ascii"), headers
        )
        token = response.get("id_token")
        if not isinstance(token, str) or len(token) > 16384:
            raise OidcError("토큰 응답에 ID 토큰이 없습니다.")
        return token

    def _pick(self, kid):
        if kid in self._keys:
            return self._keys[kid]
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return None

    def _key(self, kid):
        with self._lock:
            key = self._pick(kid)
            # An unknown kid usually means the IdP rotated keys. Re-read JWKS, but at most once a
            # minute so tokens with random kids cannot make the portal hammer the IdP.
            if key is None and monotonic() - self._keys_loaded >= JWKS_REFRESH_SECONDS:
                self._load_keys()
                key = self._pick(kid)
        if key is None:
            raise OidcError("ID 토큰 서명 키를 찾지 못했습니다.")
        return key

    def _load_keys(self):
        document = self.fetch(self.jwks_uri)
        keys = {}
        for jwk in (
            document.get("keys", []) if isinstance(document.get("keys"), list) else []
        ):
            if not isinstance(jwk, dict):
                continue
            try:
                key = rsa_public_key(jwk)
            except OidcError:
                continue
            if key:
                keys[jwk.get("kid")] = key
        self._keys = keys
        self._keys_loaded = monotonic()

    def verify_id_token(self, token, nonce, at: Optional[float] = None):
        parts = token.split(".")
        if len(parts) != 3:
            raise OidcError("ID 토큰이 JWS 형식이 아닙니다.")
        header = _json_part(parts[0], "헤더")
        if (
            header.get("alg") != "RS256"
            or "crit" in header
            or "jku" in header
            or "x5u" in header
            or "jwk" in header
        ):
            raise OidcError("허용하지 않는 ID 토큰 헤더입니다.")
        kid = header.get("kid")
        if kid is not None and not isinstance(kid, str):
            raise OidcError("ID 토큰 kid가 올바르지 않습니다.")
        key = self._key(kid)
        if not rs256_verify(
            key, (parts[0] + "." + parts[1]).encode("ascii"), b64url_decode(parts[2])
        ):
            raise OidcError("ID 토큰 서명이 맞지 않습니다.")
        claims = _json_part(parts[1], "본문")
        clock = time() if at is None else at
        if claims.get("iss") != self.issuer:
            raise OidcError("ID 토큰 발급자가 다릅니다.")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not isinstance(audiences, list) or self.client_id not in audiences:
            raise OidcError("ID 토큰 대상이 이 포털이 아닙니다.")
        azp = claims.get("azp")
        if (len(audiences) > 1 or azp is not None) and azp != self.client_id:
            raise OidcError("ID 토큰 azp가 이 포털이 아닙니다.")
        if _number(claims, "exp") <= clock - CLOCK_SKEW:
            raise OidcError("ID 토큰이 만료되었습니다.")
        if _number(claims, "iat") > clock + CLOCK_SKEW:
            raise OidcError("ID 토큰 발급 시각이 미래입니다.")
        if "nbf" in claims and _number(claims, "nbf") > clock + CLOCK_SKEW:
            raise OidcError("ID 토큰 사용 시작 전입니다.")
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode(), nonce.encode()
        ):
            raise OidcError("ID 토큰 nonce가 다릅니다.")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise OidcError("ID 토큰 sub가 올바르지 않습니다.")
        if self.required_amr:
            amr = claims.get("amr")
            if not isinstance(amr, list) or not set(self.required_amr) & {
                x for x in amr if isinstance(x, str)
            }:
                raise OidcError("IdP 로그인에 요구한 인증 수단(amr)이 없습니다.")
        return claims


def load_provider(path, environ, fetch=fetch_json):
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as exc:
        raise OidcError(f"OIDC 설정 파일을 읽지 못했습니다: {exc}") from None
    return OidcProvider.from_config(config, environ, fetch)


def new_state():
    """Return (state, nonce, verifier, challenge) for one sign-in attempt."""
    verifier, challenge = pkce_pair()
    return secrets.token_urlsafe(32), secrets.token_urlsafe(32), verifier, challenge


def link_identity(conn, store, login, issuer, subject, actor):
    store.require_row(
        conn.execute("SELECT 1 FROM accounts WHERE login=?", (login,)).fetchone()
    )
    secure_url(issuer, "issuer")
    if not 1 <= len(subject) <= 255:
        raise store.PortalError(400, "subject는 1~255자입니다.")
    existing = conn.execute(
        "SELECT login FROM identities WHERE issuer=? AND subject=?", (issuer, subject)
    ).fetchone()
    if existing and existing["login"] != login:
        raise store.PortalError(
            409,
            "이 기관 계정은 다른 포털 계정에 연결되어 있습니다. 먼저 연결을 해제하세요.",
        )
    if not existing:
        conn.execute(
            "INSERT INTO identities VALUES (?, ?, ?, ?)",
            (issuer, subject, login, store.now()),
        )
        store.event(
            conn, actor, "SSO 계정 연결", detail=f"{login} / {issuer} / {subject}"
        )


def unlink_identity(conn, store, issuer, subject, actor):
    row = store.require_row(
        conn.execute(
            "SELECT login FROM identities WHERE issuer=? AND subject=?",
            (issuer, subject),
        ).fetchone()
    )
    conn.execute(
        "DELETE FROM identities WHERE issuer=? AND subject=?", (issuer, subject)
    )
    conn.execute("DELETE FROM sessions WHERE login=? AND method='sso'", (row["login"],))
    store.event(
        conn,
        actor,
        "SSO 계정 연결 해제",
        detail=f"{row['login']} / {issuer} / {subject}",
    )
