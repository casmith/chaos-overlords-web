# Troubleshooting

Start here, always:

```bash
docker logs chaos1
```

Every stage prints a `[tag]` prefix, so a healthy startup reads as a timeline:

```
[init]    Chaos Overlords container starting
[init]    Game executable: /game/Chaos Overlords.exe
[init]    Environment validated
[display] Starting Xvfb on :0 at 640x480x24
[audio]   Starting D-Bus session bus
[audio]   Starting D-Bus system bus
[audio]   Starting PulseAudio with null sink 'chaos-out'
[stream]  Starting x11vnc on port 5900
[display] Starting Openbox
[wine]    Creating win32 Wine prefix at /config/wine (first run, this takes a moment)
[wine]    Linking game files into C:\games\Chaos
[wine]    Wine initialization complete
[game]    Launching Chaos Overlords in a 640x480 Wine desktop
```

Two lines in a normal startup look like errors and are not:

```
Xlib:  extension "DPMS" missing on display ":0".
ALSA lib seq_hw.c:540:(snd_seq_hw_open) open /dev/snd/seq failed: ...
```

The first is x11vnc probing an extension Xvfb does not have; the second is Wine
looking for an ALSA MIDI sequencer. There is deliberately no `/dev/snd` in the
container, and digital audio goes through PulseAudio regardless.

Whatever is missing from that sequence is where to look.

## Diagnostics

```bash
# overall health, one line
docker exec chaos1 healthcheck.sh

# what the screen actually shows right now
docker exec chaos1 chaos-screenshot
docker cp chaos1:/config/logs/screen.png .

# processes
docker exec chaos1 ps -ef

# verbose everything (Wine, X, PulseAudio, streaming)
DEBUG=true docker compose up -d --force-recreate chaos1
docker logs -f chaos1
```

---

## Container exits immediately

### `Game directory /game is empty` / `does not exist`

The bind mount is wrong. Check `GAME_PATH` in `.env` — it is a **host** path,
relative to the compose file:

```bash
grep GAME_PATH .env
ls "$(grep -oP '(?<=^GAME_PATH=).*' .env)"
```

### `No game executable found in /game`

Auto-detection looks for `*chaos*.exe`, then any `*.exe`, at the top level of
`/game`. If your files are one directory deeper, either point `GAME_PATH` at
that directory or set `GAME_EXE` explicitly:

```
GAME_EXE=/game/subdir/Chaos Overlords.exe
```

### `Existing prefix at /config/wine is win64, but WINEARCH=win32`

The volume was created with a different architecture. The game needs `win32`.
Reset the player (this destroys saves — see [Resetting a player](#resetting-a-player)).

---

## Black screen, no game

Work down the stack:

```bash
docker exec chaos1 xdpyinfo -display :0 | head -3    # X alive?
docker exec chaos1 pgrep -a openbox                  # WM alive?
docker exec chaos1 pgrep -a wineserver               # Wine alive?
docker exec chaos1 pgrep -af '\.exe'                 # game alive?
docker exec chaos1 chaos-screenshot                  # what is on screen?
```

If Wine is running but nothing renders, the game may have opened a dialog behind
the virtual desktop, or exited instantly. `docker logs` shows the exit status
and the restart backoff.

## The game exits over and over

The log shows this directly:

```
[game] Game exited with status 1 after 0s
[game] Restarting in 4s (consecutive failures: 2)
```

After 5 fast failures the supervisor stops and leaves the desktop up so any
error dialog stays visible. Re-run with `DEBUG=true` to get Wine's own output,
fix the cause, then:

```bash
docker exec chaos1 chaos-relaunch
```

Common causes:

- **Missing DATA files.** The game loads `DATA\` relative to its own directory.
  Confirm the links resolve:
  ```bash
  docker exec chaos1 ls -l '/config/wine/drive_c/games/Chaos'
  docker exec chaos1 ls '/config/wine/drive_c/games/Chaos/DATA' | head
  ```
- **Missing registry / serial.** The game reads its serial number from
  `HKLM\Software\Stick Man Games\Chaos Overlords\1.0`. If your copy shipped a
  `.reg` file, make sure it is in the game directory so it gets imported:
  ```bash
  docker exec chaos1 wine reg query 'HKLM\Software\Stick Man Games\Chaos Overlords\1.0'
  ```
  To re-import after fixing it:
  ```bash
  docker exec chaos1 rm /config/state/game-registry-imported
  docker restart chaos1
  ```
- **Wrong Windows version.** Try `WINE_WINDOWS_VERSION=win95` or `winxp`.

## Wine error dialog on screen

That is intended — dialogs stay visible rather than being suppressed. Read it
through the viewer, then dismiss it. If Wine reports a missing DLL, note the
name: that is the evidence needed before adding a winetricks component.

## Graphics are wrong

The game is a fixed-resolution DirectDraw-era title.

- **Black borders around the game.** The game is a fixed 640×480 title and
  Wine's virtual desktop resizes to match it, so any larger `DISPLAY_WIDTH`
  ×`DISPLAY_HEIGHT` shows up as black around the edges. 640×480 is the default
  for exactly this reason.
- **Corrupted or black rendering.** Try turning off the virtual desktop
  (`WINE_VIRTUAL_DESKTOP=false`). This is why it is configurable — but expect
  fullscreen mode changes to be less predictable without it.
- **Colours look wrong.** Try `DISPLAY_DEPTH=16`; the game's shipped
  preferences include a `prefsVidDeep` flag suggesting it cares about colour
  depth.

## No audio

Phase 1 has no browser audio path at all — raw VNC carries video only. What can
be verified now is that the game produces audio inside the container:

```bash
docker exec chaos1 pactl info
docker exec chaos1 pactl list sinks short          # expect chaos-out
docker exec chaos1 pactl list sink-inputs          # expect a Wine client while sound plays
docker exec chaos1 wine reg query 'HKCU\Software\Wine\Drivers' /v Audio
```

`Audio` should read `pulse`. If there are no sink inputs while the game plays a
sound, Wine is not reaching PulseAudio; check `PULSE_SERVER` and that
`/run/pulse/native` exists.

## Slow or stuttering

The game is 2D and undemanding; if it stutters, something else is wrong.

```bash
docker stats chaos1
```

Wine burning a full core at idle usually means a debug channel is enabled —
confirm `DEBUG=false` (which sets `WINEDEBUG=-all`).

## Saves disappeared

Saves live in the `/config` volume, inside the Wine prefix at
`/config/wine/drive_c/games/Chaos/`. They survive `docker compose down` but not
`docker volume rm`.

```bash
docker exec chaos1 ls -la '/config/wine/drive_c/games/Chaos' | grep -v ' -> '
```

Files listed there without a `->` are real files — your saves. Everything with
an arrow is a link to the read-only game mount.

## Resetting a player

This destroys that player's Wine prefix, settings and saves:

```bash
docker compose stop chaos1
docker compose rm -f chaos1
docker volume rm chaos-overlods-web_chaos1-config
docker compose up -d chaos1
```

(`docker volume ls` shows the exact name; compose prefixes it with the project
directory name.)

To re-run only the Wine configuration without losing the prefix:

```bash
docker exec chaos1 rm -f /config/state/wine-initialized
docker restart chaos1
```

## Permission errors on /config

If you replaced the named volume with a host bind mount, the directory must be
writable by uid 1000. Either `chown -R 1000:1000` it, or set `PUID`/`PGID` in
`.env` to match the owner.
