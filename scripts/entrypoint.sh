#!/bin/bash
# Stage 1: validate the environment and prepare persistent directories.
#
# Runs once, as root (or as the app user when the container itself is started
# unprivileged), before any long-running service. Anything that must be true
# for the rest of the stack to work is checked here so that failures show up at
# the top of `docker logs` instead of as a mysterious later crash.
set -euo pipefail
# shellcheck source=/dev/null
. /usr/local/bin/chaos-env

chaos_log init "Chaos Overlords container starting"
chaos_log init "config=${CONFIG_DIR} game=${GAME_DIR} display=${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}x${DISPLAY_DEPTH}"

# --- Timezone ----------------------------------------------------------------
if [ -n "${TZ}" ] && [ -f "/usr/share/zoneinfo/${TZ}" ] && [ "$(id -u)" = "0" ]; then
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
    echo "${TZ}" > /etc/timezone
fi

# --- Game files --------------------------------------------------------------
if [ ! -d "${GAME_DIR}" ]; then
    chaos_err init "Game directory ${GAME_DIR} does not exist."
    chaos_err init "Mount your Chaos Overlords files there, e.g.  -v ./chaos:${GAME_DIR}:ro"
    exit 1
fi

if [ -z "$(ls -A "${GAME_DIR}" 2>/dev/null)" ]; then
    chaos_err init "Game directory ${GAME_DIR} is empty."
    chaos_err init "Copy the installed Chaos Overlords files (the .exe, DATA/, HELP/) into it."
    exit 1
fi

resolved_exe="$(chaos_find_game_exe)"
if [ -z "${resolved_exe}" ]; then
    chaos_err init "No game executable found in ${GAME_DIR}."
    chaos_err init "Set GAME_EXE explicitly, e.g.  GAME_EXE='${GAME_DIR}/Chaos Overlords.exe'"
    exit 1
fi
if [ ! -f "${resolved_exe}" ]; then
    chaos_err init "GAME_EXE=${resolved_exe} does not exist."
    chaos_err init "Contents of ${GAME_DIR}:"
    ls -la "${GAME_DIR}" >&2 || true
    exit 1
fi
chaos_log init "Game executable: ${resolved_exe}"

# Hand the resolved path to later stages so discovery happens exactly once.
mkdir -p /run/chaos
printf '%s' "${resolved_exe}" > /run/chaos/game_exe

# --- Machine identity --------------------------------------------------------
# Without /etc/machine-id, PulseAudio falls back to asking the D-Bus system bus
# (which does not exist here) and floods the log with connection errors. Give
# each container its own id instead of baking one into the image.
if [ ! -s /etc/machine-id ] && [ "$(id -u)" = "0" ]; then
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > /etc/machine-id
    chaos_debug init "Generated machine-id $(cat /etc/machine-id)"
fi

# --- Persistent directories --------------------------------------------------
mkdir -p \
    "${CONFIG_DIR}" \
    "${CONFIG_DIR}/logs" \
    "${CONFIG_DIR}/state" \
    "${PULSE_RUNTIME_PATH}" \
    /run/chaos \
    /run/dbus \
    /tmp/.X11-unix

chmod 1777 /tmp/.X11-unix 2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
    # Optional host-uid matching, so bind-mounted /config stays writable.
    if [ -n "${PUID:-}" ] && [ "${PUID}" != "$(id -u "${APP_USER}")" ]; then
        chaos_log init "Remapping ${APP_USER} to uid ${PUID}"
        usermod -o -u "${PUID}" "${APP_USER}"
    fi
    if [ -n "${PGID:-}" ] && [ "${PGID}" != "$(id -g "${APP_USER}")" ]; then
        chaos_log init "Remapping ${APP_GROUP} to gid ${PGID}"
        groupmod -o -g "${PGID}" "${APP_GROUP}"
    fi
    chown -R "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}" /run/chaos "${PULSE_RUNTIME_PATH}"
    chown "${APP_USER}:${APP_GROUP}" /run/dbus
    chown "${APP_USER}:${APP_GROUP}" "/home/${APP_USER}" 2>/dev/null || true
else
    chaos_log init "Running unprivileged (uid $(id -u)); skipping ownership fixes"
fi

if [ ! -w "${CONFIG_DIR}" ] && [ "$(id -u)" != "0" ]; then
    chaos_err init "${CONFIG_DIR} is not writable by uid $(id -u)."
    exit 1
fi

# --- Read-only game mount sanity --------------------------------------------
if [ -w "${GAME_DIR}" ]; then
    chaos_log init "Note: ${GAME_DIR} is writable. Mounting it :ro is recommended."
fi

chaos_log init "Environment validated"
