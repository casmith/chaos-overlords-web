#!/bin/bash
# Container health check.
#
# Reports the infrastructure the player needs in order to see and control the
# game. Deliberately does NOT fail when Chaos Overlords itself is not running:
# a player quitting to the desktop is normal, and the supervisor will relaunch.
set -uo pipefail
# shellcheck source=/dev/null
. /usr/local/bin/chaos-env

fail() { echo "unhealthy: $*"; exit 1; }
ok=()

# 1. Environment validation completed.
[ -f /run/chaos/game_exe ] || fail "initialization has not completed"
ok+=("init")

# 2. The X display exists.
xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || fail "X display ${DISPLAY} is not responding"
ok+=("display")

# 3. A window manager is running.
pgrep -x openbox >/dev/null 2>&1 || fail "openbox is not running"
ok+=("openbox")

# 4. Wine is alive. Wine 10 dispatches through wineserver64 even for a win32
#    prefix, so match either name.
pgrep -x 'wineserver(32|64)?' >/dev/null 2>&1 || fail "wineserver is not running"
ok+=("wine")

# 5. Audio, when enabled.
if [ "${ENABLE_AUDIO}" = "true" ]; then
    pgrep -x pulseaudio >/dev/null 2>&1 || fail "pulseaudio is not running"
    ok+=("audio")
fi

# 6. The streaming endpoint: the browser has to be able to reach the game.
listening() {
    command -v ss >/dev/null 2>&1 || return 0
    ss -lnt 2>/dev/null | grep -q ":${1}\b"
}

if [ "${ENABLE_SELKIES}" = "true" ]; then
    listening "${WEB_PORT}" || fail "streaming port ${WEB_PORT} is not listening"
    pgrep -f 'selkies' >/dev/null 2>&1 || fail "selkies is not running"
    ok+=("stream")
fi

# 7. Raw VNC, when the operator turned it on for debugging.
if [ "${ENABLE_VNC}" = "true" ]; then
    listening "${VNC_PORT}" || fail "VNC port ${VNC_PORT} is not listening"
    ok+=("vnc")
fi

# Informational only.
if pgrep -f -i 'chaos.*\.exe' >/dev/null 2>&1; then
    ok+=("game")
else
    ok+=("game:stopped")
fi

echo "healthy: ${ok[*]}"
exit 0
