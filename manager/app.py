"""Chaos Overlords session manager.

Starts a game container per player on demand, proxies the player's browser to
it, and reaps it when nobody is watching.

Sessions publish no ports. The manager is the only route in, and it decides who
gets through: the player who owns a session opens it on their own login, and
anyone else needs the random password that session was given. So a player who
knows the URL still cannot open someone else's game, and an owner can still
deliberately hand a seat to a friend.

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
import tls
from accounts import Accounts, MIN_PASSWORD
from sessions import SessionManager
from proxy import proxy_http, proxy_websocket
from web_ui import render_page, signed_out_page, waiting_page

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
        # Two different views of the same files, and they are not
        # interchangeable. GAME_PATH_HOST is the path on the DOCKER HOST, handed
        # to the daemon to bind-mount into sessions. GAME_DIR is where those
        # files appear inside THIS container, which is what the manager can
        # actually read for the gang names.
        "game_path": os.environ.get("GAME_PATH_HOST", ""),
        "game_dir": os.environ.get("GAME_DIR", "/game"),
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
        # Wildcard domain giving every session its own hostname,
        # e.g. "chaos.kalde.in" -> <id>.chaos.kalde.in. Requires a wildcard DNS
        # record and a wildcard certificate on whatever terminates TLS. Empty
        # keeps every session on the manager's own hostname under /s/<id>/.
        "session_domain": os.environ.get("SESSION_DOMAIN", "").strip().lstrip(".").lower(),
        # HTTPS on by default: the streaming client needs a browser secure
        # context, and plain HTTP is only one on localhost. Turn it off when a
        # reverse proxy terminates TLS in front of the manager.
        "enable_https": os.environ.get("MANAGER_ENABLE_HTTPS", "true").lower() != "false",
        "https_cert": os.environ.get("MANAGER_HTTPS_CERT", ""),
        "https_key": os.environ.get("MANAGER_HTTPS_KEY", ""),
        "cert_hosts": os.environ.get("MANAGER_CERT_HOSTS", ""),
        # Extra browser origins permitted to reach a session, comma separated.
        # Same-origin is always allowed; "*" disables the check.
        "allowed_origins": os.environ.get("ALLOWED_ORIGINS", ""),
    }


# --- access control ----------------------------------------------------------
# Guards the landing page and the session API only. A session's own traffic
# under /s/ is authenticated in handle_session instead: the owner's manager
# login, or failing that the session's own password.

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

    name = (user or "").strip()[:40]
    if not name or name == cfg["admin_user"]:
        # An empty name has nothing to file sessions under, and the admin name
        # must not be claimable with a lesser password.
        return None

    accounts: Accounts = request.app["accounts"]

    # A claimed name accepts only its own password. The invite password stops
    # working for it the moment it is claimed -- that is what keeps players
    # from signing in as each other.
    if accounts.exists(name):
        return ("guest", name) if accounts.verify(name, password) else None

    # An unclaimed name is claimed with the shared invite password. The player
    # is then required to set their own before they can do anything.
    if cfg["invite_password"] and _s.compare_digest(password, cfg["invite_password"]):
        return ("claiming", name)
    return None


def session_id_from_host(request: web.Request) -> str:
    """The session a request's Host names, or "" when it names the manager.

    With SESSION_DOMAIN=chaos.kalde.in, "ab12cd.chaos.kalde.in" is session
    ab12cd and "chaos.kalde.in" is the manager itself. Only one label is
    accepted before the domain, so a deeper name cannot smuggle a session id.
    """
    domain = request.app["cfg"]["session_domain"]
    if not domain:
        return ""
    host = (request.headers.get("X-Forwarded-Host")
            or request.headers.get("Host") or "")
    host = host.split(",")[0].strip().split(":")[0].lower()
    suffix = "." + domain
    if not host.endswith(suffix):
        return ""
    label = host[: -len(suffix)]
    return label if label and "." not in label else ""


@web.middleware
async def host_routing_middleware(request: web.Request, handler):
    """Serve a session directly when its own hostname was used.

    Ahead of the auth middleware, because a session authenticates with its own
    password rather than the manager's login -- the same reason /s/ is exempt
    there.
    """
    sid = session_id_from_host(request)
    if sid:
        return await handle_session(request, sid)
    return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if (request.path.startswith("/s/")
            or request.path in ("/healthz", "/logout", "/logout/forget")):
        # /logout is exempt because it has to run: behind this middleware a
        # signed-out browser would be turned away before it could clear the
        # session cookies it still holds.
        return await handler(request)
    who = _identify(request)
    if who is None:
        # ASCII only. An HTTP field value is US-ASCII; anything above 0x7f is
        # obs-text, whose interpretation is left undefined, so a UTF-8 dash
        # here goes out as raw bytes and is read back as Latin-1 mojibake by
        # anything that displays it. This realm used to carry an em dash.
        realm = ("Chaos Overlords - admin, or your name with the invite password"
                 if request.app["cfg"]["invite_password"] else "Chaos Overlords")
        return web.Response(
            status=401, text="Authentication required.",
            headers={"WWW-Authenticate": f'Basic realm="{realm}"'})
    request["role"], request["who"] = who
    if who[0] == "claiming" and request.path not in ("/", "/api/account/password"):
        raise web.HTTPForbidden(text="Choose a password first.")
    return await handler(request)


def _visible(request: web.Request, sessions):
    """Admins see everything; a guest sees only what they own."""
    if request.get("role") == "admin":
        return list(sessions)
    if request.get("role") == "claiming":
        return []
    return [s for s in sessions if s.owner == request.get("who")]


def _may_manage(request: web.Request, session) -> bool:
    return request.get("role") == "admin" or session.owner == request.get("who")


def _signed_in_owner(request: web.Request, session) -> bool:
    """True when whoever is asking is signed in to the manager and this session
    is theirs to open.

    The per-session password is for *handing a seat to someone else* -- a friend
    with no account, a second device, someone taking over. It was never meant to
    make a player type a second password to reach their own game, and before
    accounts existed there was nothing else to identify them by. Now there is.

    Note this runs on /s/ requests, which the auth middleware deliberately does
    not guard, so a caller who is not signed in is not an error here: they fall
    through to the session password, exactly as before.
    """
    who = _identify(request)
    if who is None:
        return False
    role, name = who
    if role == "admin":
        # An admin can read every session password from the API anyway, so
        # withholding this would be theatre rather than a boundary.
        return True
    return role == "guest" and bool(session.owner) and name == session.owner


# --- UI and API --------------------------------------------------------------

async def index(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    return web.Response(
        text=render_page(_visible(request, mgr.sessions.values()), request.app["cfg"],
                         new_id=request.query.get("new", ""),
                         role=request.get("role", "admin"),
                         who=request.get("who", ""),
                         accounts=request.app["accounts"].names()),
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


async def api_set_password(request: web.Request) -> web.Response:
    """Claim a name, or change your own password. Guests only: the admin
    password lives in the environment, not in the account store."""
    if request.get("role") not in ("guest", "claiming"):
        raise web.HTTPForbidden(text="The admin password is set in the environment.")
    accounts: Accounts = request.app["accounts"]
    name = request["who"]
    data = await request.post()
    new_password = str(data.get("password", ""))
    confirm = str(data.get("confirm", ""))

    # Changing an existing password requires proving you know the current one:
    # a browser caches basic-auth credentials, so an unattended tab would
    # otherwise be enough to lock the owner out.
    if request["role"] == "guest":
        if not accounts.verify(name, str(data.get("current", ""))):
            raise web.HTTPBadRequest(text="Current password is not correct.")
    if new_password != confirm:
        raise web.HTTPBadRequest(text="The two passwords do not match.")
    try:
        accounts.set_password(name, new_password)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    # The browser is still sending the old credentials, so make it ask again.
    return web.Response(
        status=401,
        text=f"Password set for {name}. Sign in again with your new password.",
        headers={"WWW-Authenticate": f'Basic realm="Chaos Overlords - {name}"'})


async def logout(request: web.Request) -> web.Response:
    """Sign out.

    Two things have to be undone and only one of them is properly ours. The
    per-session cookies are: they are expired here and that is the end of them,
    so nobody walking up to this browser can open a game left open on it.

    The manager login is HTTP basic auth, which has no sign-out in the protocol
    -- the browser keeps sending the credentials until it decides to stop. The
    lever is the browser's own credential cache: send a *wrong* credential for
    a URL in the same protection space and the browser replaces what it had
    cached, so the next visit is challenged. The page below does that with a
    background request to /logout/forget.

    The obvious alternative -- answer 401 here and let the browser drop the
    cache -- does clear the login, but the page is not what anyone wants to
    land on: Chrome discards the body of a 401 whose prompt was dismissed and
    shows its own blank error page instead. Measured, not assumed.
    """
    resp = web.Response(
        content_type="text/html", text=signed_out_page(),
        # Belt and braces for the cookies above. Deliberately not "storage":
        # the session pages are served under this same origin, so that would
        # also wipe the Selkies client's own per-player video and audio
        # settings, which is not what signing out is for. Only honoured on a
        # secure origin, so it does nothing over plain HTTP off localhost.
        headers={"Clear-Site-Data": '"cookies"'})
    # A cookie is only cleared when the path matches the one it was set on, and
    # there are two possibilities: "/" for a session served on its own hostname,
    # "/s/<id>/" for one sharing this hostname. Which one applied is not always
    # knowable here -- a session rebuilt from its save volume after the state
    # file was lost carries no subfolder -- and a stale cookie left behind is
    # exactly the thing this endpoint exists to prevent. So clear both.
    #
    # resp.del_cookie cannot do that: it keys by cookie name, so the second call
    # replaces the first. Raw Set-Cookie headers can.
    for name in request.cookies:
        if not name.startswith(session_auth.COOKIE_PREFIX):
            continue
        sid = name[len(session_auth.COOKIE_PREFIX):]
        for path in ("/", f"/s/{sid}/"):
            resp.headers.add(
                "Set-Cookie",
                f'{name}=""; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; '
                f'Path={path}; HttpOnly; SameSite=Lax')
    return resp


async def logout_forget(request: web.Request) -> web.Response:
    """Always refuse, so the browser caches this refusal instead of a login.

    Deliberately sends no WWW-Authenticate: this is fetched in the background
    from the signed-out page, and a challenge here would put a login box in
    front of someone who has just asked to leave.
    """
    return web.Response(status=401, text="signed out")


async def api_set_owner(request: web.Request) -> web.Response:
    """Admin: hand a session to a player, or take it back to nobody.

    Any name is allowed, claimed or not: a name is claimed on first sign-in, so
    assigning a session to someone who has not signed in yet is exactly how you
    set a game up for them before they arrive. Their sessions are waiting when
    they claim the name.
    """
    if request.get("role") != "admin":
        raise web.HTTPForbidden(text="Admins only.")
    mgr: SessionManager = request.app["mgr"]
    session = mgr.sessions.get(request.match_info["sid"])
    if session is None:
        raise web.HTTPNotFound(text="No such session.")
    owner = str((await request.post()).get("owner", "")).strip()[:40]
    if owner == request.app["cfg"]["admin_user"]:
        # The admin signs in from the environment, not the account store, so a
        # session filed under that name could never be opened as its owner.
        raise web.HTTPBadRequest(text="That name belongs to the admin login.")
    await mgr.set_owner(session, owner)
    raise web.HTTPFound("/")


async def api_claim_session(request: web.Request) -> web.Response:
    """A player takes an unowned session for themselves.

    Gated on the session's own share password, and that gate is the whole
    design. An owner opens their session on their login alone, so letting any
    signed-in player claim any unowned session would hand them a session they
    were never given -- an unowned session is otherwise reachable only by
    someone holding its password. Requiring that password means claiming can
    only convert access you already have into ownership.
    """
    role = request.get("role")
    who = request.get("who", "")
    if role != "guest" or not who:
        raise web.HTTPForbidden(text="Sign in with your own name first.")
    import secrets as _s

    mgr: SessionManager = request.app["mgr"]
    cfg = request.app["cfg"]
    data = await request.post()
    sid = str(data.get("session", "")).strip()
    session = mgr.sessions.get(sid)
    if session is None:
        raise web.HTTPNotFound(text="No such session.")
    if session.owner:
        raise web.HTTPConflict(text="That session already belongs to someone.")
    if not _s.compare_digest(str(data.get("password", "")), session.password):
        raise web.HTTPForbidden(text="That is not this session's password.")
    cap = cfg["max_sessions_per_guest"]
    if cap and len(mgr.owned_by(who)) >= cap:
        raise web.HTTPConflict(text=f"You already hold {cap} sessions.")
    await mgr.set_owner(session, who)
    raise web.HTTPFound(f"/?new={session.id}")


async def api_release_account(request: web.Request) -> web.Response:
    """Admin: release a name so a forgetful player can claim it again."""
    if request.get("role") != "admin":
        raise web.HTTPForbidden(text="Admins only.")
    accounts: Accounts = request.app["accounts"]
    if not accounts.release(request.match_info["name"]):
        raise web.HTTPNotFound(text="No such account.")
    raise web.HTTPFound("/")


async def healthz(request: web.Request) -> web.Response:
    mgr: SessionManager = request.app["mgr"]
    live = sum(1 for s in mgr.sessions.values() if s.status == "ready")
    return web.json_response({"status": "ok", "sessions": len(mgr.sessions), "ready": live})


# --- session proxy -----------------------------------------------------------

async def session_proxy(request: web.Request) -> web.StreamResponse:
    """Path-routed entry point: /s/<id>/..."""
    return await handle_session(request, request.match_info["sid"])


async def handle_session(request: web.Request, sid: str) -> web.StreamResponse:
    mgr: SessionManager = request.app["mgr"]
    cfg = request.app["cfg"]
    secret: bytes = request.app["cookie_secret"]
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
        # The owner's own manager login is proof enough. The browser sends those
        # credentials here by itself -- this page is under the same origin as
        # the landing page they signed in on -- so opening your own game asks
        # for nothing. The page load issues the cookie, and the WebSocket the
        # game runs on rides that, which is the whole reason the cookie exists.
        if _signed_in_owner(request, session):
            set_cookie = True
        elif session_auth.check_basic(request.headers.get("Authorization", ""),
                                      cfg["web_user"], session.password):
            set_cookie = True
        else:
            return web.Response(
                status=401,
                text="Sign in as the player who owns this session, "
                     "or use the session's own password.",
                headers={"WWW-Authenticate": (
                    f'Basic realm="Chaos Overlords session {sid} '
                    f'- username: {cfg["web_user"]}, or your own login"')})

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

    # A host-routed session is served at the container's root, so the request
    # path passes through unchanged; a path-routed one already carries the
    # /s/<id> prefix the container was told to expect.
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
            path="/" if session.subfolder == "" else f"/s/{sid}/",
            httponly=True, samesite="Lax",
            max_age=session_auth.COOKIE_TTL,
            # Follow the real scheme: the proxy's header when there is one,
            # otherwise whether this connection is itself TLS. Marking a cookie
            # Secure over plain HTTP would make the browser drop it.
            secure=(request.headers.get("X-Forwarded-Proto", "").lower() == "https"
                    or request.scheme == "https"))
    return await proxy_http(request, target, client, inject, cookie)


# --- wiring ------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    app["client"] = aiohttp.ClientSession(
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None))
    app["cookie_secret"] = session_auth.load_or_create_secret(app["cfg"]["state_dir"])
    app["accounts"] = Accounts(app["cfg"]["state_dir"])
    app["allowed_origins"] = {o.strip() for o in app["cfg"]["allowed_origins"].split(",") if o.strip()}
    mgr = SessionManager(app["cfg"])
    app["mgr"] = mgr
    await mgr.start()
    app["reaper"] = asyncio.create_task(mgr.reap_loop())
    cfg = app["cfg"]
    retention = (f"{cfg['retention_hours']}h" if cfg["retention_hours"]
                 else "saves kept indefinitely")
    log.info("session routing: %s", f"<id>.{cfg['session_domain']}"
             if cfg["session_domain"] else "path, /s/<id>/")
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
    app = web.Application(
        middlewares=[host_routing_middleware, auth_middleware],
        client_max_size=1024 ** 3)
    app["cfg"] = load_config()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/logout", logout)
    app.router.add_get("/logout/forget", logout_forget)
    app.router.add_post("/api/account/password", api_set_password)
    app.router.add_post("/api/accounts/{name}/release", api_release_account)
    app.router.add_get("/api/sessions", api_list)
    app.router.add_post("/api/sessions", api_create)
    app.router.add_post("/api/sessions/{sid}/stop", api_stop)
    app.router.add_post("/api/sessions/{sid}/resume", api_resume)
    app.router.add_post("/api/sessions/{sid}/delete", api_delete)
    app.router.add_post("/api/sessions/{sid}/owner", api_set_owner)
    app.router.add_post("/api/sessions/claim", api_claim_session)
    app.router.add_route("*", "/s/{sid}", session_proxy)
    app.router.add_route("*", "/s/{sid}/{tail:.*}", session_proxy)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = build_app()
    cfg = app["cfg"]
    ssl_context = None
    if cfg["enable_https"]:
        ssl_context = tls.context(cfg["state_dir"], cfg["public_url"],
                                  cfg["cert_hosts"], cfg["https_cert"], cfg["https_key"])
        log.info("serving HTTPS; browsers warn once on a self-signed certificate")
    else:
        log.warning("serving plain HTTP: sessions will only work through a reverse "
                    "proxy that terminates TLS, or from localhost")
    web.run_app(app,
                host=os.environ.get("BIND_ADDR", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8000")),
                ssl_context=ssl_context,
                access_log=None)
