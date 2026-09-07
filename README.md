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
| 2 | Browser video, mouse, keyboard (Selkies) | **implemented and verified** |
| 3 | Game audio in the browser | **works** (sound effects; music needs the CD) |
| 4 | Two-instance TCP/IP multiplayer | connect verified; full game untested |
| 5 | Network discovery testing | **done** — TCP 4269, bridge networking is enough |
| 6 | Productionisation | partly in place |
| — | On-demand sessions (stretch) | **implemented** |

You open a URL and play the game. Nothing to install: no Wine, no VNC client,
no browser extension. Video, mouse and keyboard are verified working in both
Firefox and Chrome, and a full game has been started end to end through the
browser input path.

Audio rides the same connection and works. Two caveats worth knowing up front:
the in-game volume sliders do nothing (the game drives them through an API Wine
has no device for — use the browser's volume instead), and there is no music
unless you have the original CD, because the soundtrack is Red Book CD audio.
Both are explained in [docs/audio-findings.md](docs/audio-findings.md).

What testing established is in
[docs/phase-1-findings.md](docs/phase-1-findings.md) and
[docs/phase-2-findings.md](docs/phase-2-findings.md).

---

## Two ways to run it

**Fixed players** — `docker-compose.yml` starts two containers on ports 8081 and
8082. Simple, and what the quick start below covers.

**On-demand sessions** — `docker-compose.manager.yml` starts a small manager
that creates a container per player when asked, gives each its own random
password, proxies browsers to it, and shuts it down when nobody is watching.
One port for your reverse proxy to sit in front of; sessions publish none at
all. Set `INVITE_PASSWORD` and players start their own games without you.
See [docs/sessions.md](docs/sessions.md).

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

Then open a browser:

```
https://docker-host:8081     player 1
https://docker-host:8082     player 2
```

**Note the `https`.** The container serves TLS on a self-signed certificate, and
your browser will warn once — click through. This is not optional politeness:
the streaming client refuses to run outside a browser "secure context", and
plain HTTP only qualifies as one on `localhost`. Set `ENABLE_HTTPS=false` only
when a reverse proxy terminates TLS in front.

That is the whole client. There is **no login by default** — set `WEB_PASSWORD`
in `.env` to turn on basic authentication, and keep these ports on a trusted
network or behind a VPN or reverse proxy either way.

To see the screen without a browser:

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
Browser
        │  HTTP + WebSocket, one TCP port (8080)
        ▼
   Selkies ── pixelflux: reads the display, encodes H.264 on the CPU
        │  ── pcmflux:   reads the null sink monitor, encodes Opus
        │  ── XTEST:     injects mouse and keyboard back into X
        ▼
   Xvfb :0  640x480x24  ──  Openbox (no decorations, black root)
        │
   Wine (win32 prefix, virtual desktop)
        │
   Chaos Overlords            PulseAudio null sink
```

Wine sees ordinary X input events; it cannot tell a browser from a local mouse.

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
      - "8083:8080"
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
| `PATCH_DIALOG_VISIBILITY` | `true` | Patch a copy of the game in the prefix so Wine shows its Host/Join dialogs. Without it multiplayer appears to hang. Your game files are never modified |
| `ENABLE_AUDIO` | `true` | Run PulseAudio with a null sink |
| `ENABLE_SELKIES` | `true` | Browser streaming |
| `WEB_PORT` | `8080` | In-container streaming port |
| `WEB_USER` | `player` | Basic-auth username |
| `WEB_PASSWORD` | empty | Set to enable basic auth; empty means no login |
| `ENABLE_HTTPS` | `true` | Serve HTTPS on a self-signed cert; false only behind a TLS proxy |
| `WEB_SUBFOLDER` | empty | URL prefix when proxied under a subpath |
| `VIDEO_ENCODER` | `h264enc` | `h264enc`, `h264enc-striped` or `jpeg` |
| `VIDEO_FPS` | `30` | Frame rate; 15 for a low-bandwidth link |
| `VIDEO_BITRATE` | `2000` | kbps |
| `AUDIO_BITRATE` | `96000` | Opus, bits per second |
| `ENABLE_VNC` | `false` | Raw VNC on 5900, for debugging the X session |
| `VNC_PORT` | `5900` | In-container VNC port |
| `TZ` | `America/Chicago` | Container timezone |
| `DEBUG` | `false` | Verbose Wine/X/audio logging |
| `PUID` / `PGID` | `1000` | Runtime uid/gid, for bind-mounted `/config` |

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

Containers share the `chaos-net` bridge network and reach each other by name.
The game speaks plain WinSock TCP on **port 4269** — not DirectPlay — and the
joiner types the host's address, so Docker bridge networking is all it needs.

**Starting a game is not obvious**, and getting it wrong looks like the game has
frozen:

1. **Comm → WinSock** on both players. The `Comm` menu picks the transport and
   ships set to `None`, which greys out Host and Join.
2. **File → Host Game…** (Ctrl+H) on one. Pick an address, press OK.
3. **File → Join Game…** (Ctrl+J) on the other. Type the host's address.

Under Wine those dialogs are invisible unless the container patches the game —
see `PATCH_DIALOG_VISIBILITY` below and
[docs/multiplayer-findings.md](docs/multiplayer-findings.md) for the whole
story.

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

The streaming port is unauthenticated unless `WEB_PASSWORD` is set, and basic
auth over plain HTTP sends the password in the clear. Keep it on a trusted
network, or put it behind a VPN or an authenticating reverse proxy — see
[docs/networking.md](docs/networking.md).

Gamepad, webcam, microphone, file transfer and remote command execution are all
turned off in the streaming service: out of scope for this project, and less
attack surface in front of emulated 1990s software.

---

## Documentation

- [Sessions](docs/sessions.md) — on-demand containers, per-player passwords, idle teardown
- [Game files](docs/game-files.md) — what to supply and where to put it
- [Phase 1 findings](docs/phase-1-findings.md) — Wine, and why the display is 640×480
- [Phase 2 findings](docs/phase-2-findings.md) — Selkies packaging, and what was verified
- [Audio findings](docs/audio-findings.md) — why volume and music behave as they do
- [Multiplayer findings](docs/multiplayer-findings.md) — the protocol, the port, and the Wine dialog bug
- [Architecture](docs/architecture.md) — process tree, startup, filesystem layout
- [Networking](docs/networking.md) — the two network paths, ports, macvlan
- [Multiplayer testing](docs/multiplayer-testing.md) — how the ports get discovered
- [Troubleshooting](docs/troubleshooting.md) — symptom-first debugging guide
- [SPEC.md](SPEC.md) — the full project specification

---

## Legal

This repository contains no game assets. Chaos Overlords is the property of its
copyright holders. You must supply your own legally obtained copy.
