# Phase 1 findings

What testing actually established, as opposed to what the spec assumed. Recorded
per SPEC rule 16 so later phases build on measurements rather than guesses.

**Test environment**

| | |
|---|---|
| Base image | `debian:trixie-slim` (Debian 13) |
| Wine | 10.0 (`10.0~repack-6`, Debian packages) |
| s6-overlay | 3.2.3.2 |
| Prefix | `WINEARCH=win32`, confirmed `#arch=win32` in `system.reg` |
| Windows version | `win98` |
| Game build | Chaos Overlords, New World Computing, 1996 |

## Confirmed working

- The game launches, renders and plays. Verified beyond the title screen: the
  File menu opens, New Game starts, the setup screen renders, and a game begins
  with the full city map, HUD and gang panels drawn correctly.
- Mouse input reaches the game (menu bar, menu items and in-game buttons all
  respond).
- The Wine prefix persists across container restarts and is not recreated.
- Two containers run side by side with fully independent prefixes and audio.
- Clean shutdown: no Wine processes are orphaned, container exit code 0.

## Display: the game is 640×480

The spec suggested a 1024×768 virtual screen. Measured behaviour:

```
ChaosOverlords - Wine Desktop   640x480+0+0
  Chaos Overlords               640x480+0+0
```

The game requests a 640×480 mode at startup (its shipped registry has
`prefsFullScreen=1`), and **Wine's virtual desktop resizes itself to match**.
On a 1024×768 screen that leaves the game in the top-left corner surrounded by
black — 60% of the streamed pixels wasted.

**Decision:** `DISPLAY_WIDTH`/`DISPLAY_HEIGHT` default to `640`/`480`, which
frames the game exactly. Both remain configurable; raise them if you want space
around the game for Wine dialogs.

## Wine specifics

- **`wineserver64`, not `wineserver`.** Wine 10 dispatches through a 64-bit
  wineserver even for a `win32` prefix. Anything matching the process name has
  to allow for it — the health check uses `pgrep -x 'wineserver(32|64)?'`.
- **The shipped `chaosreg.reg` matters.** It carries the serial number and
  default preferences under
  `HKLM\Software\Stick Man Games\Chaos Overlords\1.0`. It is imported once, on
  first run, and the marker lives at `/config/state/game-registry-imported`.
- **`AppPath` must be rewritten.** The shipped value points at wherever the game
  was installed originally (`C:\games\Chaos`). The container recreates exactly
  that path inside the prefix and rewrites the key to match.
- **No winetricks components are needed.** The game runs on stock Wine — no
  `corefonts`, no `ddraw` overrides, no DirectPlay verbs. Per SPEC section 14
  none have been added.
- **`win98` works.** No reason found so far to try another value.

## Audio (groundwork for Phase 3)

Wine reaches PulseAudio inside the container. With the game running:

```
$ pactl list sinks short
0   chaos-out   module-null-sink.c   s16le 2ch 44100Hz   IDLE

$ pactl list clients | grep application.name
        application.name = "Chaos Overlords"
```

So the `Wine → PulseAudio → null sink` half of the topology is already proven.
Phase 3 only has to capture `chaos-out.monitor` and get it to the browser.

## D-Bus is required for a quiet startup

Without a D-Bus system bus, PulseAudio emits ~16 connection errors at startup
and then works fine anyway. That noise buried the startup timeline. Adding a
supervised system bus **and** a session bus at a fixed address
(`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/session_bus_socket`) removes all
of it. Neither bus is reachable from outside the container.

Setting `/etc/machine-id` alone was tried first and did **not** help.

## Shutdown timing

`S6_KILL_GRACETIME` is waited out unconditionally during s6's final kill stage,
so the initial value of 30 s made every `docker stop` take 33 s and get
SIGKILLed at Docker's 10 s default.

The game service already stops Wine completely on its own (measured: 0 Wine
processes remain after the service stops), so the grace time only has to cover
stragglers. With `S6_KILL_GRACETIME=3000`, a full stop takes **~6 s** and exits 0.

The service still gives the game up to 10 s to close by itself before
`wineserver -k`, so `stop_grace_period: 30s` is set in the compose file — the
Docker default of 10 s would cut a save short.

## Screenshots need `xwd -screen`

`xwd -root` captures the root window's own contents, which omits Wine's
override-redirect menu popups: clicking a menu produced a screenshot showing the
highlighted menu bar but no dropdown, even though the dropdown was there and
clickable. `chaos-screenshot` uses `xwd -root -screen`.

## Benign log lines

Two lines remain in a normal startup and are expected:

```
Xlib:  extension "DPMS" missing on display ":0".
```
x11vnc querying an extension Xvfb does not provide. Phase 1 only — x11vnc goes
away with Selkies.

```
ALSA lib seq_hw.c:540:(snd_seq_hw_open) open /dev/snd/seq failed: No such file or directory
```
Wine probing the ALSA MIDI sequencer. There is deliberately no `/dev/snd` in the
container (SPEC section 11); digital audio goes through PulseAudio and is
unaffected.

## Open for later phases

- The **Comm** menu is the multiplayer entry point. Its contents have not been
  catalogued yet — that is Phase 4.
- `commType=0` in the shipped registry suggests the game has more than one
  communications backend. Worth checking against whatever the Comm menu offers
  before assuming raw TCP.
- Whether the game writes saves into its own directory (the container is set up
  for it) is unverified; no save has been taken yet.
