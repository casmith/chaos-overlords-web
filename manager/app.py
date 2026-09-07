"""Chaos Overlords session manager.

Starts a game container per player on demand, hands out a random password for
it, proxies the player's browser to it, and reaps it when nobody is watching.

Sessions publish no ports. The manager is the only route in, which is what
makes the per-session password meaningful: another player who knows the URL
still cannot open someone else's game.

TLS is expected to terminate at a reverse proxy in front of this service.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import aiohttp
from aiohttp import web

import session_auth
from sessions import SessionManager
from proxy import proxy_http, proxy_websocket
from web_ui import render_page, waiting_page

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="[%(name)s] %(message)s")
log = logging.getLogger("manager")


def _env_map(raw: str) -> dict:
    """Parse SESSION_ENV="A=1,B=2" into a dict."""
    out = {}
    for part in raw.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_config() -> dict:
    return {
        "image": os.environ.get("SESSION_IMAGE", "chaos-overlords:latest"),
        "network": os.environ.get("SESSION_NETWORK", "chaos-net"),
        "game_path": os.environ.get("GAME_PATH_HOST", ""),
        "state_dir": os.environ.get("STATE_DIR", "/data"),
        "web_user": os.environ.get("WEB_USER", "player"),
        "tz": os.environ.get("TZ", "America/Chicago"),
        "session_env": _env_map(os.environ.get("SESSION_ENV", "")),
        "max_sessions": int(os.environ.get("MAX_SESSIONS", "6")),
        "idle_minutes": int(os.environ.get("IDLE_MINUTES", "30")),
        # 0 keeps saves indefinitely; a stopped session is only removed on request.
        "retention_hours": int(os.environ.get("RETENTION_HOURS", "0")),
        "reap_interval": int(os.environ.get("REAP_INTERVAL", "60")),
        "start_timeout": int(os.environ.get("START_TIMEOUT", "180")),
        "stop_timeout": int(os.environ.get("STOP_TIMEOUT", "30")),
        "mem_limit": os.environ.get("SESSION_MEM_LIMIT", ""),
        "cpu_limit": os.environ.get("SESSION_CPU_LIMIT", ""),
        "admin_password": os.environ.get("ADMIN_PASSWORD", ""),
        "admin_user": os.environ.get("ADMIN_USER", "admin"),
        # A second, shared password. Anyone holding it can start a session for
        # themselves and manage only their own; the admin password keeps full
        # control. Empty disables guest access entirely.
        "invite_password": os.environ.get("INVITE_PASSWORD", ""),
        "max_sessions_per_guest": int(os.environ.get("MAX_SESSIONS_PER_GUEST", "2")),
        "public_url": os.environ.get("PUBLIC_URL", "").rstrip("/"),
        # Extra browser origins permitted to reach a session, comma separated.
        # Same-origin is always allowed; "*" disables the check.
        "allowed_origins": os.environ.get("ALLOWED_ORIGINS", ""),
    }


# --- access control ----------------------------------------------------------
# Guards the landing page and the session API only. A session's own traffic
# under /s/ is authenticated by that session's password, inside the container.

def _identify(request: web.Request) -> tuple[str, str] | None:
    """Return (role, name) for the caller, or None when not authenticated.

    Role is "admin" or "guest". A guest signs in with the shared invite
    password and whatever name they like; that name is what their sessions are
    filed under, so returning with the same name shows them their own games
    again. Everyone holding the invite password is trusted not to impersonate
    each other -- they already share a secret. What actually protects a game in
    progress is its own per-session password.
    """
    import base64
    import secrets as _s

    cfg = request.app["cfg"]
    if not cfg["admin_password"] and not cfg["invite_password"]:
        return ("admin", cfg["admin_user"])         # wide open, warned about at startup

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:                               # noqa: BLE001 - malformed header
        return None

    if (cfg["admin_password"]
            and _s.compare_digest(user, cfg["admin_user"])
            and _s.compare_digest(password, cfg["admin_password"])):
        return ("admin", user)

    if cfg["invite_password"] and _s.compare_digest(password, cfg["invite_password"]):
        name = (user or "guest").strip()[:40] or "guest"
        if name == cfg["admin_user"]:
            # Not the admin password, so do not let the name claim the role.
            name = "guest"
        return ("guest", name)
    return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path.startswith("/s/") or request.path == "/healthz":
        return await handler(request)
    who = _identify(request)
    if who is None:
        realm = ("Chaos Overlords \u2014 admin, or your name with the invite password"
                 if request.app["cfg"]["invite_password"] else "Chaos Overlords")
        return web.Response(
            status=401, text="Authentication required.",
            headers={"WWW-Authenticate": f'Basic realm="{realm}"'})
    request["role"], request["who"] = who
    return await handler(request)


def _visible(request: web.Request, sessions):
    """Admins see everything; a guest sees only what they own."""
    if request.get("role") == "admin":
        return list(sessions)
    return [s for s in sessions if s.owner == request.get("who")]


def _may_manage(request: web.Request, session) -> bool:
    return request.get("role") == "admin" or session.owner == request.get("who")


# --- UI and API --------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    return web.Response(
        text=render_page(_visible(request, mgr.sessions.values()), request.app["cfg"],
                         new_id=request.query.get("new", ""),
                         role=request.get("role", "admin"),
                         who=request.get("who", "")),
        content_type="text/html")


async def api_list(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    return web.json_response(
        {"sessions": [s.public() for s in _visible(request, mgr.sessions.values())]})


async def api_create(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    data = await request.post()
    owner = "" if request.get("role") == "admin" else request.get("who", "")
    label = str(data.get("label", "")) or owner
    try:
        session = await mgr.create(label=label, owner=owner)
    except RuntimeError as exc:
        return web.Response(status=409, text=str(exc))
    raise web.HTTPFound(f"/?new={session.id}")


async def _session_or_404(request: web.Request):
    mgr: SessionManager = request.app["mgr"]
    session = mgr.sessions.get(request.match_info["sid"])
    if session is None:
        raise web.HTTPNotFound(text="No such session.")
    if not _may_manage(request, session):
        raise web.HTTPForbidden(text="That is not your session.")
    return mgr, session


async def api_stop(request: web.Request) -> web.Response:
    mgr, session = await _session_or_404(request)
    await mgr.stop(session)
    raise web.HTTPFound("/")


async def api_resume(request: web.Request) -> web.Response:
    mgr, session = await _session_or_404(request)
    await mgr.resume(session)
    raise web.HTTPFound("/")


async def api_delete(request: web.Request) -> web.Response:
    mgr, session = await _session_or_404(request)
    await mgr.delete(session)
    raise web.HTTPFound("/")


async def healthz(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    live = sum(1 for s in mgr.sessions.values() if s.status == "ready")
    return web.json_response({"status": "ok", "sessions": len(mgr.sessions), "ready": live})


# --- session proxy -----------------------------------------------------------

async def session_proxy(request: web.Request) -> web.StreamResponse:
    mgr: SessionManager = request.app["mgr"]
    cfg = request.app["cfg"]
    secret: bytes = request.app["cookie_secret"]
    sid = request.match_info["sid"]
    session = mgr.sessions.get(sid)
    if session is None:
        raise web.HTTPNotFound(text="No such session. It may have been removed.")

    # --- reject cross-site browser requests ----------------------------------
    if not session_auth.origin_allowed(request, request.app["allowed_origins"]):
        log.warning("rejected cross-origin request to %s from %r",
                    request.path, request.headers.get("Origin"))
        raise web.HTTPForbidden(text="Forbidden origin.")

    # --- authenticate this player for this session ---------------------------
    cookie = request.cookies.get(session_auth.cookie_name(sid))
    set_cookie = False
    if not session_auth.valid(secret, sid, cookie):
        if session_auth.check_basic(request.headers.get("Authorization", ""),
                                    cfg["web_user"], session.password):
            set_cookie = True
        else:
            return web.Response(
                status=401, text="This session has its own password.",
                headers={"WWW-Authenticate": (
                    f'Basic realm="Chaos Overlords session {sid} '
                    f'- username: {cfg["web_user"]}"')})

    if session.status == "stopped":
        # Bring it back rather than showing an error: the player followed a
        # link to a game that was merely idle, and their saves are still there.
        await mgr.resume(session)

    if session.status != "ready":
        # The container is still coming up. A navigation gets a page that waits
        # with the player; anything else gets a plain 503 to retry against.
        if "text/html" in request.headers.get("Accept", ""):
            return web.Response(text=waiting_page(session.label or f"Session {sid}"),
                                content_type="text/html", status=503)
        raise web.HTTPServiceUnavailable(text="The session is still starting.")

    target = f"http://{session.container_name}:8080{request.rel_url}"
    client: aiohttp.ClientSession = request.app["client"]
    mgr.touch(session)

    # The container keeps its own password check; the manager satisfies it, so
    # the browser never has to put credentials on a WebSocket handshake.
    # Present the handshake to Selkies as same-origin. Its own Origin check
    # cannot see past this proxy, so the manager enforced that rule above.
    inject = {
        "Authorization": session_auth.upstream_authorization(
            cfg["web_user"], session.password),
        "Origin": f"http://{session.container_name}:8080",
    }

    if request.headers.get("Upgrade", "").lower() == "websocket":
        mgr.conn_opened(session)
        try:
            return await proxy_websocket(
                request, target.replace("http://", "ws://", 1), client, inject)
        finally:
            mgr.conn_closed(session)

    cookie = None
    if set_cookie:
        cookie = dict(
            name=session_auth.cookie_name(sid),
            value=session_auth.issue(secret, sid),
            path=f"/s/{sid}/", httponly=True, samesite="Lax",
            max_age=session_auth.COOKIE_TTL,
            # Set Secure only when the edge really is HTTPS, or the cookie
            # would be dropped in a plain-HTTP LAN deployment.
            secure=request.headers.get("X-Forwarded-Proto", "").lower() == "https")
    return await proxy_http(request, target, client, inject, cookie)


# --- wiring ------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    app["client"] = aiohttp.ClientSession(
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None))
    app["cookie_secret"] = session_auth.load_or_create_secret(app["cfg"]["state_dir"])
    app["allowed_origins"] = {o.strip() for o in app["cfg"]["allowed_origins"].split(",") if o.strip()}
    mgr = SessionManager(app["cfg"])
    app["mgr"] = mgr
    await mgr.start()
    app["reaper"] = asyncio.create_task(mgr.reap_loop())
    cfg = app["cfg"]
    retention = (f"{cfg['retention_hours']}h" if cfg["retention_hours"]
                 else "saves kept indefinitely")
    log.info("session manager ready: image=%s network=%s idle=%dm retention=%s max=%d",
             cfg["image"], cfg["network"], cfg["idle_minutes"],
             retention, cfg["max_sessions"])
    if not cfg["admin_password"] and not cfg["invite_password"]:
        log.warning("neither ADMIN_PASSWORD nor INVITE_PASSWORD is set: anyone who "
                    "can reach this page can create and delete sessions")
    elif cfg["invite_password"]:
        log.info("guest sessions enabled: invite password set, %d session(s) per guest",
                 cfg["max_sessions_per_guest"])


async def on_cleanup(app: web.Application) -> None:
    app["reaper"].cancel()
    await app["client"].close()


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=1024 ** 3)
    app["cfg"] = load_config()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/sessions", api_list)
    app.router.add_post("/api/sessions", api_create)
    app.router.add_post("/api/sessions/{sid}/stop", api_stop)
    app.router.add_post("/api/sessions/{sid}/resume", api_resume)
    app.router.add_post("/api/sessions/{sid}/delete", api_delete)
    app.router.add_route("*", "/s/{sid}", session_proxy)
    app.router.add_route("*", "/s/{sid}/{tail:.*}", session_proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(build_app(),
                host=os.environ.get("BIND_ADDR", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8000")),
                access_log=None)
