# Phase 2 findings

Browser video, mouse and keyboard. What testing established, recorded per SPEC
rule 16.

**Test environment**

| | |
|---|---|
| Selkies | copied from `ghcr.io/selkies-project/selkies/base:main-debiantrixie` |
| Image digest | `sha256:967edbbfce557e5cf0be12d9ef7e54d6fdd2457fcb00b75cc8f4a1595e02f6e3` |
| Transport | WebSockets (Selkies default), single TCP port |
| Encoder | `h264enc`, software (x264), 30 fps |
| Browsers | Firefox 155, Google Chrome 149 |

## Packaging Selkies: why an image digest, not a package

The spec anticipated that Selkies might be hard to package (SPEC section 10).
The difficulty turned out not to be Selkies itself but its release state.

Selkies has been rewritten since its last tagged release. The old `v1.6.2`
(August 2024) is the GStreamer implementation, built for Ubuntu 20.04/22.04/
24.04 and around 100 MB of GStreamer per platform. The current implementation
on `main` is a single Python application that captures and encodes through two
native extensions (`pixelflux` for H.264/JPEG, `pcmflux` for Opus), serves its
own web client, and streams over plain WebSockets with WebRTC as an opt-in
transport. That is a far better fit for this project — one TCP port, no
GStreamer, no signalling server, no TURN.

Three routes to it were considered:

1. **PyPI.** Rejected: `selkies` on PyPI is still the old 1.6.1 GStreamer
   release, and `main` requires `pixelflux~=2.1.0` / `pcmflux~=2.1.0` while
   PyPI has 2.0.0. The current code cannot be pip-installed from published
   packages at all.
2. **The project's `.deb`.** Rejected: the documented package names include a
   `trixie` build, but they are published per release, and no release contains
   them yet.
3. **The project's own container image.** Chosen. Its `main-debiantrixie` tag
   is Debian 13 with Python 3.13 — the same distribution and interpreter as
   this image — so the native extensions match our ABI exactly.

So the build copies `/usr/local/lib/python3.13/dist-packages` and the
`/usr/local/bin/selkies*` launchers out of that image in a multi-stage build,
**pinned by digest** rather than by the moving `main-*` tag. A tag that moves
under you is not reproducible; a digest is.

The build then proves the copy actually works, rather than discovering it at
runtime:

```dockerfile
ldd pixelflux*.so | grep "not found"   # fails the build
python3 -c "import selkies, pixelflux, pcmflux"
selkies --help > /dev/null
```

Only the system libraries the extensions link against had to be added
(`libgbm1`, `libva*`, `libpixman-1-0`, `libxkbcommon0`, `libxcb-dri3-0` and the
usual X client libraries). Everything heavy — ffmpeg, x264, Opus, the
PulseAudio client — is vendored inside the wheels. Image size went from 0.55 GB
to 0.95 GB.

To update Selkies:

```bash
docker pull ghcr.io/selkies-project/selkies/base:main-debiantrixie
docker image inspect --format '{{index .RepoDigests 0}}' \
    ghcr.io/selkies-project/selkies/base:main-debiantrixie
# put that digest in the Dockerfile's SELKIES_IMAGE arg
```

## Verified

Browser tools were not available in the session, so the stream was driven two
ways: through Selkies' own WebSocket protocol (exactly what the web client
speaks), and by rendering the page in both real browsers.

**Video.** 580 binary frames / 131 KB in six seconds on a static title screen.
The low byte count is the point: Selkies sends only changed regions, so an
idle 1996 strategy game costs almost nothing.

**Mouse.** `m,<x>,<y>,<button_mask>,<scroll>` with the DOM `MouseEvent.buttons`
numbering. A click at (12,10) opened the game's File menu — 5773 pixels changed
in a 163×161 box, exactly a dropdown. A full game was then started end to end
through this path alone: File → New Game → BEGIN, ending on a live city map.

**Keyboard.** `kd,<keysym>` / `ku,<keysym>` with X11 keysyms. Escape (65307)
after the click redrew the menu region.

**Cursor.** Selkies sends the remote cursor to the client as PNG (`cursor,` text
messages). This is why Xvfb no longer runs with `-nocursor` — see below.

**Reconnect.** Three successive connect / stream / drop cycles each streamed
normally, which is what a browser page refresh is.

**Both browsers render the game.** Firefox 155 and Chrome 149 each decoded the
H.264 stream and displayed the title screen. Firefox needed `GDK_BACKEND=x11`
and `MOZ_ENABLE_WAYLAND=0` to run against a throwaway `Xvfb` for the test; that
is a property of the test harness, not of the container.

**Basic authentication.** With `WEB_PASSWORD` set: no credentials → 401, wrong
password → 401, correct → 200.

**Two players.** Two containers streamed independently on separate ports; a game
started in one left the other untouched at its title screen.

**Shutdown.** Still clean with Selkies running: ~6.7 s, exit code 0.

## `-nocursor` had to go

Phase 1 ran `Xvfb -nocursor` because nothing was looking at the pointer. Selkies
reads the cursor through XFixes and draws it in the browser, so hiding it server
side would have left every client with no visible pointer — in a game played
entirely by mouse. The flag is gone.

## Defaults chosen

| Setting | Value | Why |
|---|---|---|
| `--enable-resize` | `false` | The display is sized to the game and Xvfb cannot resize; the client must not try to fit it to the window |
| `--use-cpu` | `true` | No GPU is assumed (SPEC section 9); this skips VA-API probing |
| `--framerate` | 30 | SPEC section 35; the game is 2D and mostly static |
| `--video-bitrate` | 2000 kbps | Generous for 640×480 line art (Selkies' own default is 8000) |
| `--audio-bitrate` | 96000 | Middle of SPEC section 36's 64–128 kbps |
| `--enable-https` | `false` | TLS terminates at the reverse proxy (SPEC section 21) |
| `--enable-basic-auth` | off unless `WEB_PASSWORD` | Selkies refuses to start with auth on and no password, so this is an explicit either/or |
| gamepad / webcam / microphone / file transfer / command | all off | Out of scope (SPEC section 3) and less attack surface (SPEC section 31) |

Selkies still starts four gamepad interposer sockets and logs about them even
with `--gamepad-enabled=false`; the flag governs whether client input is
accepted, not whether the sockets are created. Harmless, but it is most of the
startup log.

## Audio: wired, not yet verified

Audio is Phase 3, but Selkies carries it on the same connection, so the path was
configured now rather than deliberately broken and re-fixed later. What is
confirmed:

```
INFO:data_websocket:pcmflux settings: device='chaos-out.monitor', bitrate=96000, channels=2
[pcmflux] SUCCESS: Opus encoder created (2 ch).
[pcmflux] Capture loop started. Device: chaos-out.monitor, Rate: 48000, Channels: 2
INFO:data_websocket:pcmflux audio capture state: running.
```

The null sink's monitor exists, pcmflux opened it, and the Opus encoder is
running. **What is not confirmed is any audio actually reaching a client**: a
test client that connected and sent `START_AUDIO` received zero binary messages
in eight seconds, and PulseAudio showed no sink input from the game and no
source output from Selkies at the time. Whether that is the game being silent at
that moment, or a client handshake step the test skipped, is Phase 3's first
question. Do not read this section as "audio works".

## Still open

- **Page title.** The browser tab reads "Selkies"; `--ui-title` changes the
  in-app title but not the served `<title>` element.
- **A thin vertical artifact** appears at the left edge of the viewport in both
  browsers. Cosmetic, cause not investigated.
- **Reverse proxy configurations** are documented in principle but none has been
  tested against a real Nginx/Traefik/Caddy deployment.
