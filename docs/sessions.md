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

Open `http://your-host:8000`, log in with `ADMIN_USER` / `ADMIN_PASSWORD`, and
press **New session**. You get a link and a password to hand to a player.

`GAME_PATH_ABS` must be an **absolute** host path. The manager asks the Docker
daemon to bind-mount it, and the daemon resolves it on the host, not inside the
manager container.

## How it fits together

```
player browser
   │  HTTPS
   ▼
your reverse proxy  (TLS terminates here)
   │  HTTP
   ▼
chaos-manager :8000
   ├── /              landing page: create, resume, stop, delete
   └── /s/<id>/*      proxied to chaos-session-<id>:8080
                            ▲
                   chaos-net, no published ports
```

Sessions publish **no ports at all**. The manager is the only route in, which
is what makes the per-session password meaningful: a player who knows another
player's URL still cannot open their game.

Because every byte passes through the manager, idle detection is exact rather
than a guess — a live WebSocket is a player watching, and its close is them
leaving.

## Lifecycle

| Event | What happens |
|---|---|
| **New session** | Container created with a random 12-character password and `WEB_SUBFOLDER=/s/<id>`. Status `starting` until Docker reports it healthy. |
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

## Security model

Three separate checks, each doing one job:

1. **The landing page** is behind `ADMIN_PASSWORD`. Creating and deleting
   sessions is an operator action. Leave it unset only on a trusted network —
   the manager logs a warning at startup if you do.
2. **Each session** is behind its own generated password. The manager checks it
   once, then issues an HMAC-signed, `HttpOnly`, `SameSite=Lax` cookie scoped to
   that session's path, valid 12 hours.
3. **Cross-site requests are refused.** A request carrying a browser `Origin`
   that is not the manager's own host gets a 403.

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
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / empty | Login for the landing page |
| `PUBLIC_URL` | empty | Base URL shown to players |
| `ALLOWED_ORIGINS` | empty | Extra permitted browser origins; `*` disables the check |
| `MAX_SESSIONS` | `6` | Refuse to create more live sessions than this |
| `IDLE_MINUTES` | `30` | Stop a session after this long with nobody watching |
| `RETENTION_HOURS` | `0` | Delete a stopped session's saves after this long; `0` keeps them indefinitely |
| `SESSION_MEM_LIMIT` | `1g` | Memory cap per session |
| `SESSION_CPU_LIMIT` | `2` | CPU cap per session |
| `SESSION_ENV` | empty | Extra env for sessions, e.g. `VIDEO_FPS=30,DEBUG=true` |
| `SESSION_IMAGE` | `chaos-overlords:latest` | Game image to start |
| `SESSION_NETWORK` | `chaos-net` | Network sessions join |
| `WEB_USER` | `player` | Username players type alongside their password |

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
