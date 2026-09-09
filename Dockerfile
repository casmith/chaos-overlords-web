# syntax=docker/dockerfile:1
#
# Chaos Overlords in a container -- Phase 1: Wine proof of concept.
#
# Builds a Debian 13 image with a 32-bit Wine, a virtual X display, Openbox and
# PulseAudio, supervised by s6-overlay. Game files are NEVER baked in; they are
# mounted read-only at /game at run time.
#
#   docker build -t chaos-overlords .
#
# ---------------------------------------------------------------------------
# Selkies source stage.
#
# Selkies is a single Python application; its published packages are built from
# a branch with no tagged release, so the reliable, reproducible way to get it
# is to copy the install out of the project's own Debian 13 image, pinned by
# digest. Same distribution, same Python 3.13, so the native extensions match
# our runtime ABI exactly.
#
# Expect this to need updating from time to time, and to find out by the build
# failing with "not found": the tag moves, and upstream garbage-collects the
# manifests nothing points at any more, digest pin or no digest pin. A local
# build can keep working long after CI stops, because the layers are still in
# the local cache -- so a green build here is not evidence the pin is still
# fetchable.
#
# To update: docker pull ghcr.io/selkies-project/selkies/base:main-debiantrixie
#            docker image inspect --format '{{index .RepoDigests 0}}' <that image>
#            then rebuild and run it, because the Selkies inside has moved too
# ---------------------------------------------------------------------------
ARG SELKIES_IMAGE=ghcr.io/selkies-project/selkies/base@sha256:7b8d7d9b2a3050d34b4e2d0f02b60ba6040d59a1338d26161d416068ec918b58
FROM ${SELKIES_IMAGE} AS selkies

# ---------------------------------------------------------------------------
# yieldsleep build stage.
#
# A ~40-line LD_PRELOAD that stops the game's message loop from spinning a whole
# core on an empty queue: 103% of a CPU down to under 5%. The reasoning and the
# measurements are in src/yieldsleep.c. Built in its own stage so the runtime
# image carries no compiler; both word sizes are needed because Wine's Windows
# processes are 32-bit here and wineserver is not.
# ---------------------------------------------------------------------------
FROM debian:trixie-slim AS yieldsleep
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gcc gcc-multilib libc6-dev libc6-dev-i386; \
    rm -rf /var/lib/apt/lists/*
COPY src/yieldsleep.c /tmp/yieldsleep.c
RUN set -eux; \
    mkdir -p /out/i386-linux-gnu /out/x86_64-linux-gnu; \
    gcc -m32 -O2 -Wall -Wextra -Werror -fPIC -shared \
        -o /out/i386-linux-gnu/yieldsleep.so /tmp/yieldsleep.c; \
    gcc      -O2 -Wall -Wextra -Werror -fPIC -shared \
        -o /out/x86_64-linux-gnu/yieldsleep.so /tmp/yieldsleep.c

FROM debian:trixie-slim

ARG S6_OVERLAY_VERSION=3.2.3.2
ARG APP_UID=1000
ARG APP_GID=1000
ARG SELKIES_PYTHON=python3.13

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Base tooling: X, window manager, audio, diagnostics.
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        dbus \
        tzdata \
        procps \
        psmisc \
        iproute2 \
        net-tools \
        xvfb \
        x11-utils \
        x11-xserver-utils \
        x11-apps \
        xdotool \
        netpbm \
        openbox \
        x11vnc \
        pulseaudio \
        pulseaudio-utils \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Runtime libraries for Selkies. Its wheels vendor the heavy pieces (ffmpeg,
# x264, Opus, PulseAudio client), so what is left are the system libraries
# pixelflux captures and encodes through, plus the Python 3.13 the copied
# install is built for.
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 \
        libgbm1 \
        libdrm2 \
        libexpat1 \
        libpixman-1-0 \
        libva2 \
        libva-drm2 \
        libva-x11-2 \
        libx11-6 \
        libx11-xcb1 \
        libxau6 \
        libxcb1 \
        libxcb-dri3-0 \
        libxdmcp6 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxtst6 \
        libice6 \
        libsm6 \
        libuuid1 \
        zlib1g \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Wine. The game is a 1997 Win32 binary, so a 32-bit prefix is required, which
# means the i386 architecture and wine32. Recommends are intentionally kept
# here: they pull the i386 audio/graphics libraries Wine needs to be useful.
# ---------------------------------------------------------------------------
RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update; \
    apt-get install -y \
        wine \
        wine32:i386 \
        wine64 \
        fonts-wine \
        winbind \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# s6-overlay: process supervision, ordered startup, child reaping, clean stop.
# ---------------------------------------------------------------------------
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/s6-noarch.tar.xz
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp/s6-x86_64.tar.xz
RUN set -eux; \
    tar -C / -Jxpf /tmp/s6-noarch.tar.xz; \
    tar -C / -Jxpf /tmp/s6-x86_64.tar.xz; \
    rm -f /tmp/s6-noarch.tar.xz /tmp/s6-x86_64.tar.xz

# ---------------------------------------------------------------------------
# Unprivileged runtime user. s6 runs as pid 1 so it can fix volume ownership,
# but every service below drops to this user -- Wine never runs as root.
# ---------------------------------------------------------------------------
RUN set -eux; \
    groupadd -g "${APP_GID}" chaos; \
    useradd -u "${APP_UID}" -g "${APP_GID}" -m -d /home/chaos -s /bin/bash chaos; \
    mkdir -p /config /game /run/chaos /run/pulse /opt/chaos /tmp/.X11-unix; \
    chmod 1777 /tmp/.X11-unix; \
    chown chaos:chaos /config /run/chaos /run/pulse

# ---------------------------------------------------------------------------
# Selkies itself: the Python package tree and its launchers.
# ---------------------------------------------------------------------------
COPY --from=selkies /usr/local/lib/${SELKIES_PYTHON}/dist-packages /usr/local/lib/${SELKIES_PYTHON}/dist-packages
COPY --from=selkies /usr/local/bin/selkies /usr/local/bin/selkies-resize /usr/local/bin/selkies-gpu-probe /usr/local/bin/

# ld.so expands $LIB in LD_PRELOAD to "lib/<triplet>", so a single
# /usr/local/$LIB/yieldsleep.so picks the right word size per process and
# neither the 32-bit game nor the 64-bit wineserver logs a preload error.
COPY --from=yieldsleep /out/ /usr/local/lib/

# Fail the build here rather than at run time if a shared library is missing:
# an unresolved symbol in pixelflux or pcmflux would otherwise surface as a
# blank browser tab with a Python traceback buried in the container log.
RUN set -eux; \
    for so in /usr/local/lib/${SELKIES_PYTHON}/dist-packages/pixelflux*.so \
              /usr/local/lib/${SELKIES_PYTHON}/dist-packages/pcmflux*.so; do \
        echo "checking ${so}"; \
        ! ldd "${so}" | grep "not found" || { ldd "${so}" | grep "not found"; exit 1; }; \
    done; \
    python3 -c "import selkies, pixelflux, pcmflux; print('selkies imports cleanly')"; \
    selkies --help > /dev/null

# The preload is silently ignored by ld.so if it is missing or the wrong class,
# and the only symptom would be a session quietly back at 100% CPU.
RUN set -eux; \
    for want in i386-linux-gnu:1 x86_64-linux-gnu:2; do \
        dir="${want%:*}"; class="${want#*:}"; \
        got="$(dd if=/usr/local/lib/$dir/yieldsleep.so bs=1 skip=4 count=1 2>/dev/null | od -An -tu1 | tr -d ' \n')"; \
        [ "$got" = "$class" ] || { echo "$dir/yieldsleep.so is ELF class $got, wanted $class"; exit 1; }; \
    done; \
    out="$(LD_PRELOAD='/usr/local/$LIB/yieldsleep.so' /bin/true 2>&1)"; \
    [ -z "$out" ] || { echo "$out"; exit 1; }; \
    echo "yieldsleep preloads cleanly"

# ---------------------------------------------------------------------------
# Project files.
# ---------------------------------------------------------------------------
COPY rootfs/ /
COPY scripts/ /opt/chaos/scripts/
COPY config/ /opt/chaos/config/

# Pin the stream to the left of the window rather than centring it. Selkies has
# no setting for this and no stylesheet hook, so the rule is inlined into its
# page; the page is copied to a temp dir at start-up, so patching the packaged
# copy is enough. See config/selkies/chaos-ui.css.
RUN set -eux; \
    python3 /opt/chaos/scripts/patch-selkies-ui.py \
        "/usr/local/lib/${SELKIES_PYTHON}/dist-packages/selkies/selkies_web/index.html" \
        /opt/chaos/config/selkies/chaos-ui.css; \
    grep -q chaos-ui-css "/usr/local/lib/${SELKIES_PYTHON}/dist-packages/selkies/selkies_web/index.html"

RUN set -eux; \
    chmod +x /opt/chaos/scripts/*.sh /usr/local/bin/chaos-*; \
    ln -sf /opt/chaos/scripts/healthcheck.sh /usr/local/bin/healthcheck.sh; \
    ln -sf /opt/chaos/scripts/detect-network.sh /usr/local/bin/detect-network.sh; \
    ln -sf /opt/chaos/scripts/init-wine.sh /usr/local/bin/init-wine.sh; \
    cp /opt/chaos/config/pulseaudio/client.conf /etc/pulse/client.conf; \
    cp /opt/chaos/config/pulseaudio/daemon.conf /etc/pulse/daemon.conf

# ---------------------------------------------------------------------------
# Defaults (SPEC section 25). Everything here is overridable per container.
# ---------------------------------------------------------------------------
ENV CONFIG_DIR=/config \
    GAME_DIR=/game \
    GAME_ARGS= \
    WINEPREFIX=/config/wine \
    WINEARCH=win32 \
    WINE_WINDOWS_VERSION=win98 \
    WINE_VIRTUAL_DESKTOP=true \
    PATCH_DIALOG_VISIBILITY=true \
    GAME_INTRO=false \
    WINE_YIELD_SLEEP_US=500 \
    DISPLAY=:0 \
    DISPLAY_WIDTH=640 \
    DISPLAY_HEIGHT=480 \
    DISPLAY_DEPTH=24 \
    ENABLE_AUDIO=true \
    ENABLE_SELKIES=true \
    WEB_PORT=8080 \
    ENABLE_HTTPS=true \
    WEB_USER=player \
    VIDEO_ENCODER=h264enc \
    JPEG_QUALITY=90 \
    JPEG_PAINT_OVER_QUALITY=95 \
    VIDEO_FPS=30 \
    VIDEO_BITRATE=2000 \
    VIDEO_FULLCOLOR=false \
    AUDIO_BITRATE=96000 \
    ENABLE_VNC=false \
    VNC_PORT=5900 \
    PULSE_RUNTIME_PATH=/run/pulse \
    PULSE_SERVER=unix:/run/pulse/native \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/session_bus_socket \
    HOME=/home/chaos \
    DEBUG=false \
    TZ=America/Chicago \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_KILL_GRACETIME=3000 \
    S6_SERVICES_GRACETIME=20000 \
    S6_VERBOSITY=1

VOLUME ["/config"]

# The browser streaming port: video, audio, mouse and keyboard all ride this
# single TCP port. 5900 is raw VNC, off unless ENABLE_VNC=true for debugging.
EXPOSE 8080
EXPOSE 5900

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["/opt/chaos/scripts/healthcheck.sh"]

ENTRYPOINT ["/init"]
