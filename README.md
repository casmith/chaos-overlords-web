# Chaos Overlords Container

Run the 1996 Windows strategy game **Chaos Overlords** in a container, one
instance per player, eventually playable from nothing but a web browser.

Game files are **not** included and are never baked into the image. You supply
your own copy.

---

## Status

| Phase | Goal | State |
|---|---|---|
| 1 | Game runs under Wine in Docker | **implemented and verified** |
| 2 | Browser video, mouse, keyboard (Selkies) | not started |
| 3 | Game audio in the browser | not started |
| 4 | Two-instance TCP/IP multiplayer | not started |
| 5 | Network discovery testing | not started |
| 6 | Productionisation | partly in place |

Phase 1 gets the game running under Wine on a virtual X display, supervised by
s6-overlay, with a persistent per-player Wine prefix. Verified end to end: the
game launches, menus respond to the mouse, a new game starts, and the city map
and HUD render correctly. What testing established — including why the display
defaults to 640×480 — is in
[docs/phase-1-findings.md](docs/phase-1-findings.md).

Until Phase 2 lands, the display is exposed over **raw VNC** for verification —
that is a development aid, not the product, and it carries no audio.

---

## Quick start

**Requirements:** Docker with the Compose plugin, on x86-64. (On Arch/Manjaro:
`pacman -S docker docker-compose`; then `systemctl start docker`.)

```bash
git clone <this repo>
cd chaos-overlords-container

# 1. Put your installed Chaos Overlords files here (git ignores this directory)
mkdir -p chaos
cp -r /path/to/installed/chaos-overlords/* chaos/

# 2. Configure
cp .env.example .env
$EDITOR .env            # set GAME_PATH to your game directory

# 3. Build and run
docker compose build
docker compose up -d
```

Watch it come up:

```bash
docker logs -f chaos1
```

Then connect a VNC viewer to `docker-host:5901` (player 1) or `:5902`
(player 2). No password — keep these ports on a trusted network.

Or, with no viewer at all:

```bash
docker exec chaos1 chaos-screenshot
docker cp chaos1:/config/logs/screen.png .
```

### Where the game files go

Anywhere you like — `GAME_PATH` in `.env` points at them, and they are mounted
read-only at `/game`:

```
GAME_PATH=./chaos
```

See [docs/game-files.md](docs/game-files.md) for the expected layout.
`.gitignore` keeps game files out of git wherever you put them.

The executable name is auto-detected. Override it only if detection picks wrong:

```
GAME_EXE=/game/Chaos Overlords.exe
```

---

## How it works

```
Browser / VNC viewer
        │
        ▼
   x11vnc  (Phase 2: Selkies + WebRTC)
        │
   Xvfb :0  640x480x24  ──  Openbox (no decorations, black root)
        │
   Wine (win32 prefix, virtual desktop)
        │
   Chaos Overlords            PulseAudio null sink
```

Everything is supervised by s6-overlay. Wine runs as the unprivileged `chaos`
user; only the supervisor is root, and only so it can fix volume ownership.

Full detail: [docs/architecture.md](docs/architecture.md).

---

## Persistent data

Each player gets their own `/config` volume:

```
/config/
├── wine/                       the 32-bit Wine prefix
│   └── drive_c/games/Chaos/    the game, as the game sees it (+ your saves)
├── logs/                       screenshots, captured logs
└── state/                      idempotency markers
```

The game files themselves stay on the read-only mount; only what the game
*writes* lands in the volume. Restarting or rebuilding the container keeps
everything.

---

## Adding players

Copy a service block in `docker-compose.yml`, give it a new name, hostname,
volume and host port:

```yaml
  chaos3:
    build: .
    image: chaos-overlords:latest
    container_name: chaos3
    hostname: chaos3
    restart: unless-stopped
    environment:
      GAME_EXE: ${GAME_EXE:-}
      TZ: ${TZ:-America/Chicago}
    volumes:
      - ${GAME_PATH:-./chaos}:/game:ro
      - chaos3-config:/config
    ports:
      - "5903:5900"
    networks:
      - chaos-net
```

...and add `chaos3-config:` under `volumes:`. Never share a `/config` volume
between players: it would mean a shared registry, shared saves and a corrupted
Wine prefix.

---

## Configuration

All of these are set in `.env` or per-service in `docker-compose.yml`.

| Variable | Default | Meaning |
|---|---|---|
| `GAME_PATH` | `./chaos` | Host directory holding the game (compose only) |
| `GAME_EXE` | auto-detected | Executable path inside the container |
| `GAME_ARGS` | empty | Extra arguments passed to the game |
| `DISPLAY_WIDTH` | `640` | Virtual screen width |
| `DISPLAY_HEIGHT` | `480` | Virtual screen height |
| `DISPLAY_DEPTH` | `24` | Colour depth |
| `WINEPREFIX` | `/config/wine` | Wine prefix location |
| `WINEARCH` | `win32` | Must stay `win32`; the game is a 32-bit binary |
| `WINE_WINDOWS_VERSION` | `win98` | `win95`, `win98`, `win2k`, `winxp` |
| `WINE_VIRTUAL_DESKTOP` | `true` | Contain the game in a Wine desktop window |
| `ENABLE_AUDIO` | `true` | Run PulseAudio with a null sink |
| `ENABLE_VNC` | `true` | Phase 1 diagnostic view |
| `VNC_PORT` | `5900` | In-container VNC port |
| `TZ` | `America/Chicago` | Container timezone |
| `DEBUG` | `false` | Verbose Wine/X/audio logging |
| `PUID` / `PGID` | `1000` | Runtime uid/gid, for bind-mounted `/config` |
| `WEB_PORT` | — | Reserved for Phase 2 |

The display defaults to 640×480 because that is the game's fixed resolution and
Wine's virtual desktop resizes to match — a larger screen just adds black
borders. `WINE_WINDOWS_VERSION=win98` has been confirmed to work; no other value
has needed trying.

---

## Operations

```bash
# health
docker exec chaos1 healthcheck.sh

# see the screen
docker exec chaos1 chaos-screenshot && docker cp chaos1:/config/logs/screen.png .

# relaunch the game after it has given up restarting
docker exec chaos1 chaos-relaunch

# multiplayer network diagnostics
docker exec chaos1 detect-network.sh

# verbose startup
DEBUG=true docker compose up -d --force-recreate
```

### Updating

```bash
git pull
docker compose build
docker compose up -d
```

`/config` volumes are untouched, so Wine prefixes and saves carry over.

### Resetting a player

Destroys that player's saves and settings:

```bash
docker compose stop chaos1
docker compose rm -f chaos1
docker volume rm "$(docker volume ls -q | grep chaos1-config)"
docker compose up -d chaos1
```

---

## Multiplayer

Containers share the `chaos-net` bridge network and can reach each other by
name. The game's own multiplayer ports have **not** been determined yet —
they will be observed rather than guessed, following the procedure in
[docs/multiplayer-testing.md](docs/multiplayer-testing.md). Findings land in
[docs/networking.md](docs/networking.md).

Note that the two network paths are independent: a remote player streams from a
container over HTTPS, while the game instances talk to each other on the
internal Docker network. Remote players do not need direct access to the game's
multiplayer protocol.

---

## Security

The container runs obsolete Windows software; treat it as untrusted.

- Wine and every other service run as an unprivileged user
- the game directory is mounted read-only
- no privileged mode, no Docker socket, no host networking, no host filesystem
  access
- `no-new-privileges:true` is set in the shipped compose file

The streaming/VNC port is unauthenticated. Keep it on a trusted network, or put
it behind a VPN or an authenticating reverse proxy — see
[docs/networking.md](docs/networking.md).

---

## Documentation

- [Game files](docs/game-files.md) — what to supply and where to put it
- [Phase 1 findings](docs/phase-1-findings.md) — what testing actually established
- [Architecture](docs/architecture.md) — process tree, startup, filesystem layout
- [Networking](docs/networking.md) — the two network paths, ports, macvlan
- [Multiplayer testing](docs/multiplayer-testing.md) — how the ports get discovered
- [Troubleshooting](docs/troubleshooting.md) — symptom-first debugging guide
- [SPEC.md](SPEC.md) — the full project specification

---

## Legal

This repository contains no game assets. Chaos Overlords is the property of its
copyright holders. You must supply your own legally obtained copy.
