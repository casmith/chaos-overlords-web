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
FROM debian:trixie-slim

ARG S6_OVERLAY_VERSION=3.2.3.2
ARG APP_UID=1000
ARG APP_GID=1000

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
    ENABLE_VNC=true \
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

# Phase 1 exposes raw VNC for verification only. Phase 2 replaces this with the
# Selkies web port.
EXPOSE 5900

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["/opt/chaos/scripts/healthcheck.sh"]

ENTRYPOINT ["/init"]
