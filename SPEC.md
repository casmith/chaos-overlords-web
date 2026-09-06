# Chaos Overlords Browser Container

## 1. Project Summary

Build a self-contained Docker image capable of running the legacy Windows game **Chaos Overlords** under Wine and exposing the game UI through a modern web browser.

The container must support:

- Wine-based execution of the Windows game
- Browser-based video/display
- Browser mouse and keyboard input
- Game audio streamed to the browser
- Persistent Wine configuration and save data
- Multiple independent container instances
- Chaos Overlords TCP/IP multiplayer between instances
- Operation on a local LAN and, optionally, remote access through a VPN/reverse proxy
- Game files supplied separately by the user and never embedded in the source repository

The final user experience should be:

1. Start the container.
2. Navigate to a URL.
3. See Chaos Overlords running.
4. Hear game audio.
5. Interact with the game using mouse and keyboard.
6. Create or join a TCP/IP multiplayer game.

---

# 2. Primary Goal

The primary deployment model is:

```text
Browser
   |
   | HTTPS / WebRTC
   v
Browser streaming service
   |
   +-- Video
   +-- Audio
   +-- Mouse
   +-- Keyboard
   |
   v
Linux graphical session
   |
   v
Wine
   |
   v
Chaos Overlords
```

The browser must not require:

- VNC software
- Wine
- Game installation
- Browser extensions
- Native application installation

A modern Chromium or Firefox browser should be sufficient.

---

# 3. Non-Goals

Do not initially attempt to implement:

- Automated public Internet matchmaking
- Game modification
- Game patching beyond what is necessary for Wine compatibility
- Save-game synchronization between players
- Mobile-specific controls
- Controller/gamepad support
- Kubernetes deployment
- Cloud hosting
- Automatic game-file downloading
- DRM circumvention
- Redistribution of copyrighted game assets

These can be addressed later.

---

# 4. Source Repository

Suggested repository name:

```text
chaos-overlords-container
```

Suggested structure:

```text
chaos-overlords-container/
├── README.md
├── LICENSE
├── docker-compose.yml
├── docker-compose.example.yml
├── .env.example
├── .gitignore
├── Dockerfile
├── scripts/
│   ├── entrypoint.sh
│   ├── init-wine.sh
│   ├── launch-game.sh
│   ├── healthcheck.sh
│   └── detect-network.sh
├── config/
│   ├── wine/
│   ├── openbox/
│   ├── pulseaudio/
│   └── selkies/
├── docs/
│   ├── architecture.md
│   ├── networking.md
│   ├── troubleshooting.md
│   └── multiplayer-testing.md
└── game/
    └── README.md
```

The actual `game/` contents must be excluded through `.gitignore`.

Example:

```gitignore
game/*
!game/README.md
```

---

# 5. Base Operating System

Use a stable Debian or Ubuntu Linux base.

Preferred:

```text
Debian 13
```

Alternative:

```text
Ubuntu 24.04 LTS
```

Avoid excessively minimal images if they make Wine, X11, audio, or WebRTC dependencies significantly harder to maintain.

The image should prioritize reproducibility over minimizing image size.

---

# 6. Wine

Use Wine with a dedicated 32-bit Wine prefix.

Environment:

```text
WINEARCH=win32
WINEPREFIX=/config/wine
```

The container must create the prefix automatically during first startup if it does not exist.

Example persisted path:

```text
/config/wine
```

The `/config` directory must live on a Docker volume.

The initialization process must be idempotent.

Running the container multiple times must not recreate or destroy the Wine prefix.

---

# 7. Game Files

The user will provide Chaos Overlords game files.

Do not put the game files in the Docker image source repository.

Support one of the following locations:

```text
/game
```

Preferred Docker mount:

```yaml
volumes:
  - ./game:/game:ro
```

The container should identify the game executable through an environment variable.

Example:

```text
GAME_EXE=/game/CHAOS.EXE
```

Do not hard-code the executable name if avoidable.

Example configuration:

```text
GAME_EXE=/game/CHAOS.EXE
GAME_ARGS=
```

If `GAME_EXE` is absent, startup must fail with a useful error.

---

# 8. Graphical Environment

The game should run in a controlled virtual Linux desktop.

Preferred stack:

```text
Xorg/Xvfb
+
Openbox
+
Wine virtual desktop
```

Do not run a heavyweight desktop environment unless required.

Preferred virtual screen resolution:

```text
1024x768
```

Make resolution configurable:

```text
DISPLAY_WIDTH=1024
DISPLAY_HEIGHT=768
DISPLAY_DEPTH=24
```

Wine should preferably launch the game within its own virtual desktop to prevent resolution or fullscreen changes from breaking the streaming session.

Conceptual command:

```bash
wine explorer \
  /desktop=ChaosOverlords,1024x768 \
  "$GAME_EXE"
```

If testing determines that Chaos Overlords works better without Wine's virtual desktop, make this configurable.

Example:

```text
WINE_VIRTUAL_DESKTOP=true
```

---

# 9. Browser Streaming

## Primary Implementation

Use **Selkies** as the primary browser streaming system.

Requirements:

- Browser video streaming
- Mouse input
- Keyboard input
- Audio streaming
- Support Firefox and Chromium
- No browser plugins
- WebRTC transport where supported

The game is graphically lightweight, so CPU-based video encoding should be the initial implementation.

Do not require GPU passthrough.

GPU encoding can be added as an optional feature later.

---

# 10. Browser Streaming Fallback

If Selkies proves excessively difficult to package reliably, implement:

```text
X11
  |
TigerVNC
  |
websockify
  |
noVNC
```

However, noVNC alone does not satisfy the audio requirement.

If noVNC is used, audio must be implemented through an additional browser-compatible stream.

Selkies therefore remains strongly preferred.

Do not silently remove audio support merely because noVNC works.

---

# 11. Audio

Run a virtual Linux audio server inside the container.

Preferred:

```text
PulseAudio
```

Wine should output through PulseAudio.

The browser streaming system should capture that audio stream.

Audio requirements:

- Game sound effects
- Music if supported by the game
- Browser playback
- Independent audio per container
- No dependency on a physical host sound card

Create a PulseAudio null sink if necessary.

Conceptual topology:

```text
Wine
 |
PulseAudio
 |
Virtual Sink
 |
Selkies
 |
Browser
```

The container should not attempt to use `/dev/snd` unless explicitly configured.

---

# 12. Process Supervision

Multiple processes will run inside the container.

Potential processes:

```text
X server
Openbox
PulseAudio
Selkies
Wine server
Chaos Overlords
```

Use either:

- s6-overlay
- supervisord
- a robust custom entrypoint

Preferred:

```text
s6-overlay
```

The container must:

- terminate cleanly
- reap child processes
- stop Wine cleanly
- expose meaningful container health
- restart failed critical services where appropriate

---

# 13. Startup Sequence

The startup sequence should approximately be:

```text
1. Validate environment
2. Verify game executable exists
3. Create persistent directories
4. Initialize Wine prefix if missing
5. Apply required Wine registry/configuration
6. Start virtual display
7. Start Openbox
8. Start PulseAudio
9. Start browser streaming service
10. Launch Chaos Overlords
11. Monitor application
```

Initialization and runtime startup should be separate scripts.

---

# 14. Wine Initialization

Implement:

```text
scripts/init-wine.sh
```

It should:

- create the prefix
- suppress unnecessary Wine first-run dialogs
- configure Windows version if needed
- configure Wine audio
- configure desktop resolution
- install only required winetricks components

Do not add random winetricks packages without evidence that the game needs them.

Possible dependencies that may eventually be tested include:

```text
corefonts
ddraw overrides
legacy DirectPlay components
```

But these should only be introduced if actual testing requires them.

---

# 15. Windows Compatibility Mode

Make the reported Wine Windows version configurable.

Default:

```text
Windows 98
```

or whichever version proves most reliable during testing.

Environment:

```text
WINE_WINDOWS_VERSION=win98
```

Other possible values should include:

```text
win95
win98
win2k
winxp
```

Document whichever setting proves best.

---

# 16. Networking Requirements

Chaos Overlords multiplayer must work between multiple containers.

There are two networking scenarios.

## Scenario A — Direct TCP/IP

If Chaos Overlords allows joining a game by entering an IP address, standard Docker networking may be sufficient.

Example:

```text
chaos1
chaos2
chaos3
```

All containers on:

```text
chaos-net
```

The containers should be able to communicate directly.

---

# 17. Multiplayer Discovery

If the game performs broadcast-based LAN discovery, normal Docker bridge networking may not behave correctly.

Therefore, support a second networking mode using:

```text
macvlan
```

or:

```text
ipvlan
```

The objective is to allow each Chaos container to appear as an independent host on the physical LAN.

Example:

```text
192.168.15.21 chaos1
192.168.15.22 chaos2
192.168.15.23 chaos3
192.168.15.24 chaos4
```

Document macvlan configuration separately.

Do not require macvlan until testing determines whether Docker bridge networking works.

---

# 18. Multiplayer Port Discovery

Do not guess the game's multiplayer ports.

During testing:

1. Launch one instance.
2. Host a multiplayer game.
3. Observe listening sockets.
4. Capture traffic while another instance joins.

Use Linux tools such as:

```bash
ss -lntup
```

```bash
tcpdump
```

```bash
conntrack
```

Document:

- TCP ports
- UDP ports
- broadcast behavior
- source/destination patterns

Once known, add the ports to documentation and sample Docker Compose configurations.

---

# 19. Multiple Player Instances

The intended deployment model is one container per player.

Example:

```text
Player 1 browser -> chaos1
Player 2 browser -> chaos2
Player 3 browser -> chaos3
Player 4 browser -> chaos4
```

Each container must have its own:

```text
/config
```

volume.

This prevents:

- shared registry settings
- save conflicts
- player-name conflicts
- Wine prefix corruption

---

# 20. Docker Compose

Provide a working example for at least two players.

Conceptually:

```yaml
services:

  chaos1:
    image: chaos-overlords:latest
    environment:
      GAME_EXE: /game/CHAOS.EXE
      DISPLAY_WIDTH: 1024
      DISPLAY_HEIGHT: 768
    volumes:
      - ./game:/game:ro
      - chaos1-config:/config
    networks:
      chaos-net:

  chaos2:
    image: chaos-overlords:latest
    environment:
      GAME_EXE: /game/CHAOS.EXE
      DISPLAY_WIDTH: 1024
      DISPLAY_HEIGHT: 768
    volumes:
      - ./game:/game:ro
      - chaos2-config:/config
    networks:
      chaos-net:

networks:
  chaos-net:

volumes:
  chaos1-config:
  chaos2-config:
```

The actual browser ports depend on the selected Selkies implementation.

Make external web ports configurable.

Example:

```text
WEB_PORT=8080
```

---

# 21. Reverse Proxy

The application should work behind common reverse proxies.

Target compatibility:

- Nginx
- Traefik
- Caddy

Document special requirements for WebRTC/WebSocket connections.

The container itself should not attempt to manage publicly trusted TLS certificates.

TLS termination should normally occur at the reverse proxy.

---

# 22. Authentication

Do not assume the internal streaming service is safe to expose publicly.

At minimum document integration with:

- Authentik
- Authelia
- Tailscale
- WireGuard
- reverse-proxy basic authentication

Authentication does not need to be part of v1 container code unless the browser streaming component requires it.

---

# 23. Remote Multiplayer

There are two independent network paths:

```text
Browser -> streamed desktop container
```

and:

```text
Chaos Overlords container -> Chaos Overlords container
```

Do not confuse them.

A remote player may control a game instance over HTTPS/WebRTC while Chaos itself communicates only over the internal server/LAN network.

This means remote users do not necessarily need direct network access to the Chaos multiplayer protocol.

This deployment model is preferred.

---

# 24. Environment Variables

Support at least:

```text
GAME_EXE
GAME_ARGS

DISPLAY_WIDTH
DISPLAY_HEIGHT
DISPLAY_DEPTH

WINEPREFIX
WINEARCH
WINE_WINDOWS_VERSION
WINE_VIRTUAL_DESKTOP

WEB_PORT

TZ

PLAYER_NAME
```

Potential future variables:

```text
ENABLE_GPU
VIDEO_BITRATE
VIDEO_FPS
AUDIO_BITRATE
WEB_PASSWORD
ENABLE_AUDIO
```

Provide `.env.example`.

---

# 25. Default Values

Suggested defaults:

```text
GAME_EXE=/game/CHAOS.EXE

DISPLAY_WIDTH=1024
DISPLAY_HEIGHT=768
DISPLAY_DEPTH=24

WINEPREFIX=/config/wine
WINEARCH=win32
WINE_WINDOWS_VERSION=win98
WINE_VIRTUAL_DESKTOP=true

WEB_PORT=8080

TZ=America/Chicago
```

Do not assume `PLAYER_NAME` unless the game requires one.

---

# 26. Health Check

Implement:

```text
scripts/healthcheck.sh
```

The health check should verify at minimum:

- streaming server responds
- X display exists
- Wine server is running

Optional:

- Chaos Overlords process exists

Example Docker health semantics:

```text
healthy
starting
unhealthy
```

Avoid declaring the container unhealthy merely because the user closes the game intentionally.

If the game exits, either:

- restart it automatically

or:

- expose a lightweight way to relaunch it

Automatic restart is preferred for v1.

---

# 27. Game Restart

If `CHAOS.EXE` exits unexpectedly:

```text
wait 2 seconds
restart game
```

Avoid restart loops.

Use exponential or capped retry behavior if startup fails repeatedly.

After several failures, leave the container running so the streaming desktop can display the error.

---

# 28. Debug Mode

Support:

```text
DEBUG=true
```

Debug mode should increase logging for:

- Wine
- display server
- PulseAudio
- Selkies
- networking

Normal mode should suppress excessive Wine diagnostic noise.

Potential Wine logging:

```text
WINEDEBUG=-all
```

for normal operation.

Debug mode may use:

```text
WINEDEBUG=+warn,+err
```

or more targeted channels.

---

# 29. Logging

All runtime logs should go to stdout/stderr where practical.

Docker users should be able to use:

```bash
docker logs chaos1
```

to diagnose startup.

Logs must make startup stages obvious.

Example:

```text
[init] Checking game files
[init] Wine prefix already exists
[display] Starting X server
[audio] Starting PulseAudio
[stream] Starting Selkies
[game] Launching Chaos Overlords
```

---

# 30. Persistence

Persist:

```text
/config
```

Possible contents:

```text
/config/
├── wine/
├── saves/
├── logs/
└── state/
```

The initial implementation may store saves inside the Wine prefix.

Do not persist temporary X11, PulseAudio, or streaming runtime state unless necessary.

---

# 31. Security

The container runs obsolete Windows software.

Treat it as untrusted legacy software.

Security requirements:

- do not run Wine as root
- use a dedicated unprivileged Linux user
- game directory mounted read-only
- persistent `/config` writable only by container user
- no Docker socket
- no privileged container mode
- no unnecessary Linux capabilities
- no host filesystem access
- no host networking by default

Provide an optional hardened Compose configuration using:

```yaml
security_opt:
  - no-new-privileges:true
```

Drop unnecessary capabilities.

---

# 32. Network Isolation

Recommend that deployments place these containers in either:

- a dedicated Docker network
- a legacy-games VLAN
- an isolated server network

The containers should not need access to sensitive infrastructure.

If Internet access is unnecessary after setup, document how outbound WAN access can be blocked.

---

# 33. User Experience

Visiting the URL should ideally show only the game.

Avoid exposing a normal Linux desktop unless necessary.

Preferred appearance:

```text
Browser
  -> black background
  -> Chaos Overlords window
```

Openbox should not display unnecessary panels, menus, or desktop icons.

If Wine displays error dialogs, they must remain accessible through the browser.

---

# 34. Browser Behavior

Test with:

```text
Firefox
Chromium / Chrome
```

Browser requirements:

- mouse clicks
- mouse movement
- keyboard input
- audio playback
- fullscreen browser mode
- reconnect after page refresh

If audio autoplay is blocked by browser security, provide an obvious click-to-enable-audio mechanism.

---

# 35. Frame Rate

Chaos Overlords does not require high frame rates.

Default target:

```text
30 FPS
```

Allow:

```text
VIDEO_FPS=30
```

Potential low-bandwidth mode:

```text
15 FPS
```

Do not waste CPU encoding at 60 FPS unless testing demonstrates a benefit.

---

# 36. Audio Bitrate

Low to moderate quality is sufficient.

Suggested:

```text
64–128 kbps Opus
```

Prioritize compatibility and low latency over fidelity.

---

# 37. Network Latency

Input latency under approximately:

```text
150 ms
```

is more than adequate for the game.

The architecture should prioritize stability over ultra-low latency.

---

# 38. Container Image Architecture

Target initially:

```text
linux/amd64
```

ARM support is not required.

The legacy Windows executable is x86, and the primary expected Docker hosts are x86_64.

---

# 39. Build Process

Provide:

```bash
docker build -t chaos-overlords .
```

and:

```bash
docker compose up -d
```

Documentation should clearly explain where game files must be placed.

Example:

```text
./game/CHAOS.EXE
```

or the actual discovered path.

---

# 40. First-Run Behavior

On first launch:

1. Detect missing Wine prefix.
2. Create prefix.
3. Configure Wine.
4. Launch the game.

Do not require manual `winecfg` interaction unless absolutely necessary.

Any repeatable Wine configuration should be automated through:

- registry files
- `wine reg`
- environment variables
- scripts

---

# 41. Game Installation Variant

The initial implementation should assume already-installed game files supplied by the user.

If this does not work because registry keys or installer-created data are required, implement support for:

```text
/game-installer/setup.exe
```

and perform one-time installation into:

```text
/config/wine
```

But only add this complexity if required.

---

# 42. Development Phases

## Phase 1 — Wine Proof of Concept

Goal:

Chaos Overlords runs under Wine.

Deliverables:

- Dockerfile
- 32-bit Wine prefix
- mounted game files
- working graphical X session
- game launches successfully

Do not implement browser streaming until this works.

---

## Phase 2 — Browser Video/Input

Goal:

Control the game from a browser.

Deliverables:

- Selkies integration
- browser rendering
- mouse control
- keyboard control
- reconnect support

Acceptance:

A user with no local Wine/VNC software can play the game from Firefox.

---

## Phase 3 — Audio

Goal:

Hear game audio in the browser.

Deliverables:

- PulseAudio virtual sink
- Wine audio configuration
- Selkies audio capture
- browser playback

Acceptance:

Game music/sound effects are audible through the browser.

---

## Phase 4 — Two-Instance Multiplayer

Goal:

Two containers play together.

Deploy:

```text
chaos1
chaos2
```

Acceptance:

- instance 1 hosts
- instance 2 joins
- both remain stable
- multiplayer turn state synchronizes

Document required ports.

---

## Phase 5 — Network Discovery Testing

Determine whether the game uses:

- direct TCP
- UDP
- broadcast discovery
- DirectPlay
- another mechanism

Capture and document actual traffic.

If Docker bridge networking is sufficient, keep it.

If not, implement macvlan/ipvlan example.

---

## Phase 6 — Productionization

Add:

- health check
- persistent volumes
- robust process supervision
- graceful shutdown
- restart behavior
- `.env.example`
- multi-player Compose examples
- security hardening
- documentation

---

# 43. Acceptance Criteria

The project is v1 complete when all of the following are true.

### Game execution

```text
[ ] Chaos Overlords runs inside Docker.
[ ] Game files are externally mounted.
[ ] Wine prefix persists across container restarts.
[ ] Game automatically launches.
```

### Browser

```text
[ ] Browser displays the game.
[ ] Mouse works.
[ ] Keyboard works.
[ ] Page refresh reconnects.
[ ] Firefox works.
[ ] Chromium works.
```

### Audio

```text
[ ] Game audio reaches browser.
[ ] No physical sound device is required.
[ ] Multiple containers have independent audio.
```

### Multiplayer

```text
[ ] Two containers can communicate.
[ ] One can host a game.
[ ] Another can join.
[ ] Multiplayer remains stable for an entire game session.
[ ] Required ports/protocols are documented.
```

### Container behavior

```text
[ ] Container runs as non-root.
[ ] Game directory is mounted read-only.
[ ] Config persists.
[ ] Container shuts down cleanly.
[ ] Logs are readable through docker logs.
[ ] Health check exists.
```

### Documentation

```text
[ ] README contains setup instructions.
[ ] Multiplayer networking is documented.
[ ] Game-file placement is documented.
[ ] Troubleshooting guide exists.
```

---

# 44. Testing Matrix

Test at least:

| Test | Requirement |
|---|---|
| Fresh container | Wine initializes automatically |
| Existing `/config` | Wine initialization is skipped |
| Missing game executable | Clear startup error |
| Firefox client | Working |
| Chromium client | Working |
| Browser refresh | Session reconnects |
| Container restart | Persistent game/Wine state retained |
| Two containers | Can play multiplayer |
| Audio | Audible through browser |
| Game exit | Game can restart |
| Server reboot | Stack recovers automatically |

---

# 45. Networking Diagnostic Script

Create:

```text
scripts/detect-network.sh
```

It should help discover multiplayer behavior.

Useful output:

```text
container IP
routing table
listening TCP sockets
listening UDP sockets
Wine processes
active connections
```

Optionally provide instructions for packet capture.

Do not require tcpdump in the final minimal runtime image unless useful.

---

# 46. Development Convenience

Provide a development Compose profile exposing useful diagnostic access.

Optional:

```text
SSH
raw VNC
Wine debug logs
PulseAudio inspection
```

These should not be enabled in normal production deployment.

---

# 47. README

README should contain:

## Quick Start

```bash
git clone ...
cd chaos-overlords-container

mkdir game
cp /path/to/game/files/* game/

docker compose build
docker compose up -d
```

Then:

```text
http://docker-host:PORT
```

Also explain:

- persistent data location
- adding players
- multiplayer networking
- troubleshooting audio
- resetting a player's Wine prefix
- updating the container

---

# 48. Resetting a Player

Document a clean reset procedure.

Example:

```bash
docker compose down
docker volume rm chaos1-config
docker compose up -d
```

Warn that this removes saves/configuration.

---

# 49. Stretch Goals

Do not implement these until v1 works.

Possible later enhancements:

### Session Landing Page

A small web application showing:

```text
Chaos Overlords

Player 1 — Launch
Player 2 — Launch
Player 3 — Launch
Player 4 — Launch
```

---

### Dynamic Instances

Create containers on demand:

```text
Create Player Session
```

and destroy them after inactivity.

---

### Player Authentication

Map authenticated users to specific containers.

---

### Save Backups

Automatically archive Wine save directories.

---

### Turn Notifications

Potentially detect when the local player's turn begins and send:

- ntfy
- Discord
- browser notification

---

### Kubernetes

Eventually package as:

```text
Deployment/StatefulSet
Service
Ingress
PVC
```

but Docker Compose should remain the reference implementation.

---

### Game Library

Generalize the architecture so other Windows 95/98 games can use the same infrastructure.

Potential future abstraction:

```text
legacy-browser-game
```

with per-game configuration:

```yaml
game:
  executable: CHAOS.EXE
  resolution: 1024x768
  windowsVersion: win98
  networkMode: tcpip
```

Chaos Overlords should remain the first supported game.

---

# 50. Important Implementation Rules

Claude Code should follow these rules while building:

1. Work through the phases sequentially.
2. Do not redesign the project unless testing proves an assumption wrong.
3. Prefer working, maintainable solutions over clever abstractions.
4. Keep all game assets outside Git.
5. Never embed copyrighted game files in the Docker image or repository.
6. Automate Wine configuration wherever possible.
7. Do not require manual GUI setup during ordinary container deployment.
8. Keep one Wine instance per player container.
9. Use Selkies as the preferred browser streaming implementation.
10. Keep GPU acceleration optional.
11. Start with Docker bridge networking.
12. Only introduce macvlan/ipvlan if multiplayer testing proves it necessary.
13. Discover actual Chaos Overlords network ports rather than guessing them.
14. Keep the game directory read-only.
15. Run Wine and browser-streaming processes as an unprivileged user.
16. Maintain detailed documentation as discoveries are made during testing.
17. If a blocker is encountered, investigate and implement the most reasonable solution rather than stopping to ask the user for architecture decisions.
18. Only stop for user input when physical/external information is genuinely required, such as missing game files.

---

# 51. Expected Final Result

A finished deployment should resemble:

```text
                         Docker Host

      ┌──────────────────────────────────────────────┐
      │                                              │
      │   chaos1             chaos2                  │
      │ ┌───────────┐      ┌───────────┐             │
      │ │ Selkies   │      │ Selkies   │             │
      │ │ X/Openbox │      │ X/Openbox │             │
      │ │ PulseAudio│      │ PulseAudio│             │
      │ │ Wine      │      │ Wine      │             │
      │ │ Chaos     │      │ Chaos     │             │
      │ └─────┬─────┘      └─────┬─────┘             │
      │       │                  │                   │
      │       └──── Multiplayer ─┘                   │
      │                                              │
      └───────────────┬──────────────────────────────┘
                      │
              Reverse Proxy / VPN
                      │
             ┌────────┴────────┐
             │                 │
         Player 1          Player 2
         Browser           Browser
```

Each player interacts only through a browser.

Each player receives:

- video
- audio
- mouse input
- keyboard input

Chaos Overlords itself communicates directly between the Wine instances over TCP/IP.

The resulting system should make a Windows 95-era multiplayer game behave like a modern browser-hosted application without modifying the game itself.