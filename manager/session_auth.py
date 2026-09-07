"""Per-session authentication, done by the manager rather than the browser.

A session is protected by its own generated password. The obvious way to check
it is to let the session container's HTTP basic auth do the work and have the
browser replay cached credentials -- but a browser is not reliably willing to
put an Authorization header on a WebSocket handshake, and the whole game is a
WebSocket. A player would get the page and then sit on "Connecting" forever.

So the manager authenticates instead: basic auth once, then a signed cookie.
Cookies *are* sent on same-origin WebSocket handshakes, so the stream carries
the same proof as the page did. The manager then adds the upstream
Authorization header itself, and the container keeps its own password check as
a second line of defence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path

COOKIE_TTL = 12 * 3600          # a sitting is hours, not days
COOKIE_PREFIX = "chaos_s_"


def load_or_create_secret(state_dir: str) -> bytes:
    """A manager-wide signing key, stable across restarts so cookies survive one."""
    path = Path(state_dir) / "cookie.key"
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def cookie_name(session_id: str) -> str:
    return f"{COOKIE_PREFIX}{session_id}"


def issue(secret: bytes, session_id: str) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret, f"{session_id}.{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def valid(secret: bytes, session_id: str, value: str | None) -> bool:
    if not value or "." not in value:
        return False
    ts, _, sig = value.partition(".")
    if not ts.isdigit() or time.time() - int(ts) > COOKIE_TTL:
        return False
    expected = hmac.new(secret, f"{session_id}.{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def check_basic(header: str, user: str, password: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        got_user, _, got_password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:                               # noqa: BLE001 - malformed header
        return False
    return (hmac.compare_digest(got_user, user)
            and hmac.compare_digest(got_password, password))


def upstream_authorization(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def origin_allowed(request, extra_allowed: set[str]) -> bool:
    """Same-origin check for state-changing browser requests.

    Selkies does this itself, but only for requests that reach it looking
    same-origin. Behind this proxy every request carries the *manager's* origin,
    so the container's check would reject every browser and pass every
    non-browser client -- exactly backwards. The proxy therefore rewrites the
    upstream Origin and enforces the rule here instead, where it knows what the
    public hostname actually is.

    A request with no Origin is allowed: that is a non-browser client, which
    still has to hold the session cookie or password.
    """
    import urllib.parse
    origin = request.headers.get("Origin")
    if not origin:
        return True
    host = (request.headers.get("X-Forwarded-Host")
            or request.headers.get("Host") or "").split(",")[0].strip()
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if origin in extra_allowed or "*" in extra_allowed:
        return True
    if not host:
        return False
    # Compare hostnames: the Host header carries a port and the Origin may not,
    # or vice versa, so a strict netloc comparison rejects legitimate requests.
    return bool(parts.hostname) and parts.hostname == host.split(":")[0]
