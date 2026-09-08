# On-demand sessions

A small service that creates a game container per player when someone asks for
one, gives each its own random password, proxies browsers to it, and shuts it
down when nobody is watching.

This is SPEC section 49's "Dynamic Instances" and "Session Landing Page"
stretch goals. The fixed two-player `docker-compose.yml` still works and is
simpler; use this when you want players to come and go.

## Quick start

```bash
docker compose -f docker-compose.yml build              # the game image
cp .env.example .env                                    # set GAME_PATH_ABS, ADMIN_PASSWORD
docker compose -f docker-compose.manager.yml up -d
```

Open `https://your-host:8000` — note the **https** — log in with `ADMIN_USER` /
`ADMIN_PASSWORD`, and press **New session**. You get a link, which opens
straight into the game for you, plus a username and password that let someone
without an account of their own take that seat.

The manager generates its own self-signed certificate, so your browser warns
once; click through. This is not optional politeness: the streaming client
refuses to run outside a browser *secure context*, and plain HTTP only counts as
one on `localhost`. Served over HTTP, a session opened from any other machine
loads the page and then never starts. Put the addresses players will actually
type into `MANAGER_CERT_HOSTS` (e.g. `192.168.1.50,chaos.lan`) and the
certificate names them, leaving only the untrusted-issuer warning.

Behind a reverse proxy that terminates TLS, set `MANAGER_ENABLE_HTTPS=false`
and let the proxy present a real certificate.

`GAME_PATH_ABS` must be an **absolute** host path. The manager asks the Docker
daemon to bind-mount it, and the daemon resolves it on the host, not inside the
manager container.

## How it fits together

```
player browser
   │  HTTPS -- a reverse proxy's real certificate, or, with nothing in front,
   │           the manager's own self-signed one
   ▼
chaos-manager :8000
   ├── /              landing page: create, resume, stop, delete
   └── /s/<id>/*      proxied to chaos-session-<id>:8080
                            ▲
                   chaos-net, no published ports
```

Sessions publish **no ports at all**. The manager is the only route in, and it
decides who gets through: a player who knows another player's URL still cannot
open their game.

Because every byte passes through the manager, idle detection is exact rather
than a guess — a live WebSocket is a player watching, and its close is them
leaving.

## Lifecycle

| Event | What happens |
|---|---|
| **New session** | `SESSION_IMAGE` is pulled if the tag has moved, then a container is created with a random 12-character password and `WEB_SUBFOLDER=/s/<id>`. Status `starting` until Docker reports it healthy. |
| **Player connects** | The manager counts the WebSocket and keeps the session alive for as long as it is open. |
| **Nobody watching for `IDLE_MINUTES`** | Container stopped and removed. **The volume is kept**, so saves, settings and the Wine prefix survive. |
| **Player returns to their link** | The session is recreated against the same volume automatically. They see a "starting" page for ~15 s, then the game as they left it. |
| **Stopped for `RETENTION_HOURS`** | Only when set. At the default of `0` nothing is ever removed automatically. |
| **Delete** | Container and volume removed immediately. The only way a save is lost. |

Stopping a session gives Wine 30 seconds to close the game and flush its saves
first, the same as `docker stop` on a fixed instance.

## Saves are kept indefinitely

`RETENTION_HOURS=0` is the default and means exactly that: a stopped session is
removed only when someone presses **Delete**. Stopping a session frees its CPU
and memory, never its data.

Two things make that safe to rely on.

**The save volume describes itself.** Each session's volume carries the session
id, password, name and creation time as Docker labels, so the saves do not
depend on the manager's `sessions.json` surviving. If that state is lost — a
wiped volume, a rolled-back deployment, a rebuilt host with the volumes intact —
the manager rebuilds the sessions from the volumes on startup:

```
[sessions] adopted save volume chaos-session-f33j9p-config as session f33j9p
```

The player's original link and password keep working. This is tested by
deleting the manager's entire state volume and confirming the save comes back.

**Nothing else deletes a volume.** The reaper removes containers; only an
explicit delete, or a retention window you set yourself, removes data.

### The disk cost

A session volume is roughly **650 MB** — mostly the Wine prefix, plus the
patched copy of the game. Keeping saves forever is a real commitment, so watch
it:

```bash
docker system df -v | grep chaos-session
```

To prune by hand, delete the session from the landing page, or:

```bash
docker volume rm chaos-session-<id>-config
```

If you would rather have it swept automatically, set `RETENTION_HOURS` to a
number of hours; stopped sessions idle for longer than that are removed with
their data.

## Session names

Sessions are named after the game's own gangs — `sewer-rats`, `pudding-clowns`,
`brothers-of-the-blade` — rather than random characters, because a session name
ends up in a URL that gets read out loud and typed by someone else. There are 90
of them, which is more than anyone will run at once, and they recycle: a name is
free again as soon as the session holding it is deleted. A stopped session keeps
its name, because its saves are still there and the name is how a player finds
them again.

The names are read from **the operator's own game files at run time**, from
`DATA/Gangs` in the directory mounted at `/game`. They are never baked into the
image: they are the game's content, the image is published publicly, and the
same reasoning that keeps the executable and data files out of it applies to a
list extracted from them.

If the game files are not mounted, or the file cannot be parsed, sessions fall
back to short random identifiers and the manager says so once at startup.
Nothing else changes.

Past 90 live sessions a name is reused with a numeric suffix
(`sewer-rats-2`). That is a formality rather than a plan.

## Giving each session its own hostname

By default every session lives under the manager's hostname at `/s/<id>/`.
Set `SESSION_DOMAIN` to a wildcard domain and each gets a hostname instead:

```
SESSION_DOMAIN=chaos.example.com

chaos.example.com          the landing page
ab12cd.chaos.example.com   a session
```

This needs three things in front of the manager, and no per-session work in any
of them:

1. A **wildcard DNS record** for `*.chaos.example.com`. Cloudflare allows
   wildcards to be proxied on every plan now, so a tunnel works.
2. A **wildcard certificate** covering `chaos.example.com` *and*
   `*.chaos.example.com` — a wildcard does not cover the bare name.
3. A proxy that passes the original `Host` through, since that is what the
   manager routes on. `X-Forwarded-Host` is honoured too.

Nothing is created or destroyed in DNS as sessions come and go: one wildcard
covers all of them, so a session is reachable the moment its container is up,
and there is nothing to clean up when it is reaped. That is why this is
preferred over minting a record per session — and why the manager needs no DNS
credentials.

The mode is recorded per session when its container is created, not read live,
so changing `SESSION_DOMAIN` cannot desync a running container from the URL
prefix it was started with. Existing sessions pick up the new mode the next time
they are resumed.

Separate hostnames also mean the browser treats sessions as separate origins, so
cookies and storage are isolated between them rather than merely path-scoped.

## Letting players start their own

Set `INVITE_PASSWORD` and you stop being the bottleneck. It is a second, shared
password you hand out once instead of creating a session per player and sending
three credentials each time.

A player opens the manager, and at the browser prompt types **any name they
like** plus the invite password. That **claims** the name: they are made to
choose their own password before they can do anything else, and from then on the
invite password will not open that name again. They get their own page:

- a **New session** button, capped at `MAX_SESSIONS_PER_GUEST` (default 2)
- only **their own** sessions listed, each with a link and a share password
- **Open**, **Stop**, **Resume** and **Delete** on those, and nothing else
- **Change your password**

The name is what their sessions are filed under, so signing in tomorrow shows
them the same games. Admin still sees and controls everything, and can
**Release** a name whose password has been forgotten — the games survive it.

```
ADMIN_PASSWORD=...     full control: every session, delete anyone's
INVITE_PASSWORD=...    create your own, see and manage only your own
```

**The trust level is "a group of friends".** Claiming is first-come: the invite
password opens any name nobody has taken yet, so it is worth telling players to
sign in once early rather than on the night. Once a name is claimed it takes its
own password and nothing else, which is what keeps players from signing in as
each other. If you need more than that — enrolment you control, revocation,
audit — put a real identity provider (Authelia, Authentik, Tailscale) in front of
the manager instead.

Someone signing in with the admin username but the invite password gets guest
access, not admin — the name never confers the role.

Leave `INVITE_PASSWORD` empty to keep session creation admin-only.

## Security model

Three separate checks, each doing one job:

1. **The landing page** is behind `ADMIN_PASSWORD`, with optional guest access
   through `INVITE_PASSWORD` and a claimed account as above. With neither
   password set the page is open to anyone who can reach it — the manager logs a
   warning at startup if so.
2. **Each session** admits two kinds of caller: the **player who owns it**, on
   the login they already used for the landing page, and **anyone else** holding
   that session's generated password. Either way the manager issues an
   HMAC-signed, `HttpOnly`, `SameSite=Lax` cookie scoped to that session's path
   and valid 12 hours; the WebSocket the game runs on rides that cookie.
3. **Cross-site requests are refused.** A request carrying a browser `Origin`
   that is not the manager's own host gets a 403.

### Why the owner is not asked for the session password

Before accounts existed, the generated password was the only thing that could
tell one player from another at `/s/<id>/`, so everyone typed it — including the
person whose game it was. Now the manager knows who is asking, and a player
opening their own game is asked for nothing: `/s/` is on the same origin as the
landing page they signed in on, so the browser replays those credentials by
itself, the page load turns them into the session cookie, and the stream follows.

Verified in a real browser rather than assumed: signed in as `clay`, navigating
to `clay`'s own session returns 200 and the game, and navigating to a session
`clay` does not own returns 401 in the same session.

The per-session password stays, because it does a different job now — **handing
a seat to someone else**. A friend with no account, a second device, someone
taking over while you cook dinner: send them the link, the username and the
password and they are in, with no account and no admin involvement. It is also
what the container's own basic auth checks, so a session is never unprotected
even to something that reaches it directly on the Docker network.

An admin opens any session without its password. They can already read every
password from `/api/sessions`, so withholding it would be theatre.

### Why the manager authenticates instead of the container

The obvious design is to let the session container's own basic auth do the work
and have the browser replay its cached credentials. That fails: a browser is not
reliably willing to put an `Authorization` header on a WebSocket handshake, and
the whole game *is* a WebSocket. The page loads and then sits on "Connecting"
forever.

Cookies **are** sent on same-origin WebSocket handshakes, so the manager
authenticates once over HTTP, issues a cookie, and adds the upstream
`Authorization` header itself. The container keeps its own password check as a
second line of defence — it simply never has to be satisfied by the browser.

### Why the manager also does the Origin check

Selkies refuses cross-origin WebSocket upgrades, which is correct. But behind a
proxy every request carries the *manager's* origin, so the container's own check
would reject every real browser and wave through every non-browser client —
exactly backwards. The proxy therefore presents a same-origin handshake upstream
and enforces the rule itself, where it knows the real public hostname.

### The Docker socket

The manager needs the Docker API to create containers, and that is effectively
root on the host. It is granted to the manager alone; the session containers,
which run the untrusted 1996 software, never get it (SPEC section 31).

If that trade is not acceptable, put a filtering proxy such as
`tecnativa/docker-socket-proxy` in front of the socket, allowing only
`CONTAINERS`, `VOLUMES` and `IMAGES`, and point the manager at it with
`DOCKER_HOST=tcp://socket-proxy:2375`.

## Reverse proxy

TLS terminates at your proxy. Two things the manager needs from it:

- **WebSocket upgrade**, forwarded on every path under the manager.
- **`X-Forwarded-Proto: https`**, so the session cookie is issued with the
  `Secure` flag. Without it the cookie still works, but it is not marked secure.

Do not set an idle read timeout below your `IDLE_MINUTES`, or the proxy will cut
streams that are working fine.

Nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

Caddy, which handles the upgrade and `X-Forwarded-Proto` on its own:

```caddy
chaos.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Set `PUBLIC_URL=https://chaos.example.com` so the landing page shows players a
link they can actually use.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MANAGER_PORT` | `8000` | Host port for the manager |
| `GAME_PATH_ABS` | — | **Absolute** host path to the game files (required) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / empty | Full-control login for the landing page |
| `INVITE_PASSWORD` | empty | Shared password letting players start their own sessions; empty keeps creation admin-only |
| `MAX_SESSIONS_PER_GUEST` | `2` | How many sessions one guest name may hold |
| `PUBLIC_URL` | empty | Base URL shown to players |
| `MANAGER_ENABLE_HTTPS` | `true` | Serve HTTPS on a self-signed certificate; `false` only behind a TLS proxy |
| `MANAGER_CERT_HOSTS` | empty | Extra names/IPs to put in that certificate |
| `SESSION_DOMAIN` | empty | Wildcard domain giving each session its own hostname; empty keeps `/s/<id>/` |
| `ALLOWED_ORIGINS` | empty | Extra permitted browser origins; `*` disables the check |
| `MAX_SESSIONS` | `6` | Refuse to create more live sessions than this |
| `IDLE_MINUTES` | `30` | Stop a session after this long with nobody watching |
| `RETENTION_HOURS` | `0` | Delete a stopped session's saves after this long; `0` keeps them indefinitely |
| `SESSION_MEM_LIMIT` | `1g` | Memory cap per session |
| `SESSION_CPU_LIMIT` | `2` | CPU cap per session. A ceiling, not a reservation: a session idles at about 0.05 of a core (see [cpu-findings.md](cpu-findings.md)), so this only caps the damage if the yield shim is turned off |
| `SESSION_ENV` | empty | Extra env for sessions, e.g. `VIDEO_FPS=30,DEBUG=true` |
| `SESSION_IMAGE` | `chaos-overlords:latest` | Game image to start |
| `SESSION_NETWORK` | `chaos-net` | Network sessions join |
| `WEB_USER` | `player` | Username players type alongside their password |

## Updating the game image

Sessions are created by the manager at run time, so **the session image is not
one of the images `docker compose` knows about** — `docker compose pull` updates
nginx, certbot, cloudflared and the manager, and does not touch it. Before this
was handled here, a host could be updated in every visible way and still start
every new session on a months-old build.

The manager therefore pulls `SESSION_IMAGE` itself whenever it creates a
container, which covers both a new session and one being resumed after the idle
reaper removed its container. A registry that is unreachable is not fatal: it
logs once and carries on with the local copy.

A session whose container still exists keeps the image it started with — Docker
has no way to swap an image under a running container. To move a live session
onto a new build, **Stop** it and open it again: stop removes the container, and
opening the link recreates it.

## Operations

```bash
# what is running
curl -su admin:PASSWORD http://localhost:8000/api/sessions | python3 -m json.tool

# the manager's own health
curl -s http://localhost:8000/healthz

# session containers
docker ps --filter label=chaos.managed=true

# logs
docker logs chaos-manager
docker logs chaos-session-<id>
```

Session state lives in `/data/sessions.json` on the `manager-data` volume, plus
the containers and volumes themselves. On startup the manager reconciles them:
a session whose container is gone is marked stopped, a managed container with no
session record is removed (its password cannot be recovered), and a save volume
with no session record is **adopted** from its own labels.

`/data/sessions.json` **contains session passwords** in plain text, mode `0600`.
It is on a private volume, and anyone who can read it could already read the
container environment through the Docker socket the manager holds.

## Multiplayer between sessions

Sessions all join `chaos-net` and reach each other by container name, so
multiplayer works exactly as in [multiplayer-findings.md](multiplayer-findings.md).
Players need each other's **container IP**, which the host's dialog shows:
`Comm → WinSock`, then `File → Host Game…` on one and `File → Join Game…` on the
other.

## Known limits

- Session creation is not rate-limited beyond `MAX_SESSIONS`.
- The reaper reconciles on a fixed interval, so a session may live up to
  `REAP_INTERVAL` seconds past its idle deadline.
- A session that never becomes healthy stays `starting` until `START_TIMEOUT`,
  then stops being polled; it is reaped on the normal idle path.
- Restarting the manager drops the live-connection counts to zero, so a session
  with a connected player can be reaped a cycle early. It comes straight back
  when the player's browser reconnects.
