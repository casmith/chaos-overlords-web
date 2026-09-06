# Architecture

## Current status

**Phase 1 (Wine proof of concept) — implemented and verified.**
Chaos Overlords runs, renders and plays under Wine on a virtual X display
inside the container. What testing established is recorded in
[phase-1-findings.md](phase-1-findings.md).
Browser streaming (Phase 2), browser audio (Phase 3) and multiplayer testing
(Phases 4–5) are not implemented yet. Raw VNC is exposed as a temporary
verification path only.

## Process tree

```
/init                        (s6-overlay, pid 1, root)
 ├── init-config   [oneshot]  validate env, prepare /config, fix ownership
 ├── dbus          [longrun]  D-Bus system bus                (root, then messagebus)
 ├── dbus-session  [longrun]  D-Bus session bus               (user: chaos)
 ├── xvfb          [longrun]  Xvfb :0  640x480x24             (user: chaos)
 ├── init-wine     [oneshot]  create/refresh the Wine prefix  (user: chaos)
 ├── openbox       [longrun]  window manager, no decorations  (user: chaos)
 ├── pulseaudio    [longrun]  null sink "chaos-out"           (user: chaos)
 ├── x11vnc        [longrun]  Phase 1 diagnostic view         (user: chaos)
 └── game          [longrun]  wine explorer /desktop=... CHAOS (user: chaos)
```

The two D-Bus buses exist only to keep PulseAudio quiet: without them it logs
about a dozen connection failures at startup and then works anyway. Neither bus
is reachable from outside the container. See
[phase-1-findings.md](phase-1-findings.md).

s6 enforces the ordering through `dependencies.d`. Because s6 considers a
longrun "up" the moment it is exec'd, services that need a usable X display
additionally block on `chaos-wait-x`, which polls `xdpyinfo` until the display
answers.

Only s6's supervision tree runs as root, and only so that it can chown the
`/config` volume and optionally remap the runtime uid. Every service that
touches the game — Wine included — runs as the unprivileged `chaos` user.

## Startup sequence

Matching SPEC section 13:

1. `init-config` validates the environment: `/game` exists, is non-empty, and
   contains a runnable executable. The resolved path is written to
   `/run/chaos/game_exe` so discovery happens exactly once.
2. `xvfb` starts the virtual display, and the D-Bus buses come up.
3. `init-wine` creates the 32-bit prefix if it is missing, then reasserts the
   registry settings and the game file links (both cheap and idempotent).
4. `openbox` takes over window management and paints the root black.
5. `pulseaudio` starts with a null sink.
6. `x11vnc` exposes the display (Phase 1 only).
7. `game` launches Chaos Overlords inside a Wine virtual desktop and supervises
   it, restarting with capped backoff.

Failure in either oneshot aborts the container
(`S6_BEHAVIOUR_IF_STAGE2_FAILS=2`), so a missing game file produces an
immediate, readable failure rather than a black screen.

## Filesystem layout

| Path | Mount | Contents |
|---|---|---|
| `/game` | host bind, read-only | your Chaos Overlords install |
| `/config` | named volume, per player | Wine prefix, saves, logs, state |
| `/config/wine` | — | the 32-bit Wine prefix (`WINEPREFIX`) |
| `/config/wine/drive_c/games/Chaos` | — | the game, as the game sees it |
| `/config/state` | — | idempotency markers |
| `/config/logs` | — | screenshots and any captured logs |
| `/opt/chaos` | image | scripts and config shipped with the image |
| `/run/chaos` | tmpfs | per-boot runtime state |
| `/run/pulse` | tmpfs | PulseAudio socket |

## How the game sees its files

The game's shipped registry (`chaosreg.reg`) sets
`HKLM\Software\Stick Man Games\Chaos Overlords\1.0\AppPath` to `C:\games\Chaos`,
and the game loads `DATA\` relative to its own directory. Two constraints
collide here: the game directory must be writable (the game writes saves next to
itself), but the mounted game files must stay read-only.

The resolution: `C:\games\Chaos` is a **real directory inside the Wine prefix**,
and every top-level entry from `/game` is **symlinked** into it.

```
/config/wine/drive_c/games/Chaos/
├── Chaos Overlords.exe -> /game/Chaos Overlords.exe   (link, read-only)
├── DATA                -> /game/DATA                  (link, read-only)
├── HELP                -> /game/HELP                  (link, read-only)
└── <savegame>                                          (real file, persisted)
```

Reads resolve through the links to the read-only mount; anything new the game
creates lands in the writable prefix and survives container restarts.
`init-wine.sh` refreshes the links on every start, removes links whose target
disappeared, and never overwrites a real file — a save game is never clobbered
by a relink.

## Idempotency

`init-wine.sh` runs on every start. It distinguishes three kinds of work:

- **Once, ever** — creating the prefix, importing the game's `.reg`. Guarded by
  marker files under `/config/state`.
- **Every start, safe to repeat** — Windows version, audio driver, virtual
  desktop size, `AppPath`, game file links. Reasserted so that changing an
  environment variable takes effect on the next restart.
- **Never** — deleting or recreating an existing prefix. If `WINEARCH` does not
  match an existing prefix, the container fails with an explicit message rather
  than silently rebuilding it.

## Game supervision

`launch-game.sh` runs the game in a loop:

- normal exit after ≥60 s → treated as the player quitting; relaunch after 2 s
- fast exit → counted as a startup failure; backoff 2, 4, 8, 16, 30 s (capped)
- 5 consecutive fast failures → stop relaunching, but keep the desktop alive so
  any Wine error dialog stays visible; `docker exec <c> chaos-relaunch` retries

On `SIGTERM` the supervisor asks the game to close and waits up to 10 s for it
to do so, letting Wine flush its registry and saves, before falling back to
`wineserver -k`. Measured: no Wine process survives that, and a full
`docker stop` takes about 6 seconds and exits 0. The compose file sets
`stop_grace_period: 30s` because Docker's 10 s default would cut a save short.

## Deliberate omissions

- **No GPU.** The game is 2D and 800×600-era; CPU rendering is plenty.
- **No winetricks packages.** None have been shown to be necessary. Per SPEC
  section 14, they get added only when testing proves a need.
- **No `/dev/snd`.** Audio is entirely virtual, so containers never contend for
  a host sound card and each player gets independent audio.
