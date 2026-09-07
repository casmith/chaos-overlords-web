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
# To update: docker pull ghcr.io/selkies-project/selkies/base:main-debiantrixie
#            docker image inspect --format '{{index .RepoDigests 0}}' <that image>
# ---------------------------------------------------------------------------
ARG SELKIES_IMAGE=ghcr.io/selkies-project/selkies/base@sha256:967edbbfce557e5cf0be12d9ef7e54d6fdd2457fcb00b75cc8f4a1595e02f6e3
FROM ${SELKIES_IMAGE} AS selkies

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

# ---------------------------------------------------------------------------
# Project files.
# ---------------------------------------------------------------------------
COPY rootfs/ /
COPY scripts/ /opt/chaos/scripts/
COPY config/ /opt/chaos/config/

RUN set -eux; \
    chmod +x /opt/chaos/scripts/*.sh /usr/local/bin/chaos-*; \
    ln -sf /opt/chaos/scripts/healthcheck.sh /usr/local/bin/healthcheck.sh; \
    ln -sf /opt/chaos/scripts/detect-network.sh /usr/local/bin/detect-network.sh; \
    ln -sf /opt/chaos/scripts/init-wine.sh /usr/local/bin/init-wine.sh; \
    cp /opt/chaos/config/pulseaudio/client.conf /etc/pulse/client.conf

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
    DISPLAY=:0 \
    DISPLAY_WIDTH=640 \
    DISPLAY_HEIGHT=480 \
    DISPLAY_DEPTH=24 \
    ENABLE_AUDIO=true \
    ENABLE_SELKIES=true \
    WEB_PORT=8080 \
    ENABLE_HTTPS=false \
    WEB_USER=player \
    VIDEO_ENCODER=h264enc \
    VIDEO_FPS=30 \
    VIDEO_BITRATE=2000 \
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
