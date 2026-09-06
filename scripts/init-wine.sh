#!/bin/bash
# Stage 2: create and configure the Wine prefix, then expose the game files
# inside it.
#
# This script is idempotent by design: it is safe to run on every container
# start. An existing prefix is never recreated or wiped -- only the parts that
# are cheap and safe to reassert (registry settings, game file links) are
# refreshed.
set -euo pipefail
# shellcheck source=/dev/null
. /usr/local/bin/chaos-env

STATE_MARKER="${CONFIG_DIR}/state/wine-initialized"
REG_MARKER="${CONFIG_DIR}/state/game-registry-imported"

# winemenubuilder writes .desktop files we have no use for and logs noisily.
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES};winemenubuilder.exe=d"
export DISPLAY

game_exe="$(cat /run/chaos/game_exe 2>/dev/null || chaos_find_game_exe)"

wine_quiet() {
    if [ "${DEBUG}" = "true" ]; then
        wine "$@"
    else
        wine "$@" >/dev/null 2>&1
    fi
}

# --- Prefix architecture guard ----------------------------------------------
if [ -f "${WINEPREFIX}/system.reg" ]; then
    existing_arch=$(grep -m1 '^#arch=' "${WINEPREFIX}/system.reg" | cut -d= -f2 || true)
    if [ -n "${existing_arch}" ] && [ "${existing_arch}" != "${WINEARCH}" ]; then
        chaos_err wine "Existing prefix at ${WINEPREFIX} is ${existing_arch}, but WINEARCH=${WINEARCH}."
        chaos_err wine "Either set WINEARCH=${existing_arch} or delete the config volume to start over."
        exit 1
    fi
fi

# --- Create the prefix -------------------------------------------------------
if [ -f "${STATE_MARKER}" ] && [ -f "${WINEPREFIX}/system.reg" ]; then
    chaos_log wine "Wine prefix already exists at ${WINEPREFIX} (${WINEARCH})"
else
    chaos_log wine "Creating ${WINEARCH} Wine prefix at ${WINEPREFIX} (first run, this takes a moment)"
    mkdir -p "${WINEPREFIX}"
    if ! wine_quiet wineboot --init; then
        chaos_err wine "wineboot failed. Re-run with DEBUG=true for Wine output."
        exit 1
    fi
    wineserver -w
    mkdir -p "$(dirname "${STATE_MARKER}")"
    date -Iseconds > "${STATE_MARKER}"
    chaos_log wine "Wine prefix created"
fi

# --- Registry configuration (reasserted every start; all idempotent) ---------
chaos_log wine "Reported Windows version: ${WINE_WINDOWS_VERSION}"
wine_quiet reg add 'HKCU\Software\Wine' /v Version /t REG_SZ /d "${WINE_WINDOWS_VERSION}" /f || true
wine_quiet winecfg -v "${WINE_WINDOWS_VERSION}" || true

if [ "${ENABLE_AUDIO}" = "true" ]; then
    chaos_log wine "Audio driver: pulse"
    wine_quiet reg add 'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d pulse /f || true
else
    chaos_log wine "Audio disabled"
    wine_quiet reg add 'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d '' /f || true
fi

# Keep Wine's own explorer from painting a desktop wallpaper/shell we do not want.
wine_quiet reg add 'HKCU\Software\Wine\Explorer\Desktops' /v ChaosOverlords \
    /t REG_SZ /d "${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" /f || true

# --- Mirror the game into the prefix ----------------------------------------
# The game's shipped registry points AppPath at C:\games\Chaos, and the game
# loads DATA\ relative to its own directory. We create that directory for real
# inside the (writable, persisted) prefix and symlink each top-level entry from
# the read-only mount into it. Saves and other files the game creates then land
# in the prefix instead of failing against a read-only mount.
chaos_log wine "Linking game files into ${GAME_WIN_DIR}"
mkdir -p "${GAME_UNIX_DIR}"

# Drop links whose target disappeared (e.g. the game mount changed).
find "${GAME_UNIX_DIR}" -maxdepth 1 -type l ! -exec test -e {} \; -print -delete 2>/dev/null | \
    while read -r stale; do chaos_log wine "Removed stale link $(basename "${stale}")"; done

linked=0
for entry in "${GAME_DIR}"/*; do
    [ -e "${entry}" ] || continue
    name="$(basename "${entry}")"
    target="${GAME_UNIX_DIR}/${name}"
    if [ -L "${target}" ]; then
        if [ "$(readlink "${target}")" != "${entry}" ]; then
            ln -sfn "${entry}" "${target}"
        fi
    elif [ -e "${target}" ]; then
        # A real file here is something the game (or the user) wrote. Never
        # clobber it -- it may be a save game.
        chaos_debug wine "Keeping existing file ${name} in the prefix"
        continue
    else
        ln -s "${entry}" "${target}"
    fi
    linked=$((linked + 1))
done
chaos_log wine "${linked} game entries available at ${GAME_WIN_DIR}"

# --- Import the game's shipped registry file --------------------------------
# Chaos Overlords ships chaosreg.reg containing its serial number and default
# preferences; without it the game re-runs first-time setup on every launch.
if [ ! -f "${REG_MARKER}" ]; then
    game_reg="${GAME_REG:-}"
    if [ -z "${game_reg}" ]; then
        game_reg="$(find "${GAME_DIR}" -maxdepth 1 -type f -iname '*.reg' | sort | head -n1)"
    fi
    if [ -n "${game_reg}" ] && [ -f "${game_reg}" ]; then
        chaos_log wine "Importing game registry file $(basename "${game_reg}")"
        win_reg="$(winepath -w "${game_reg}" 2>/dev/null || echo "${game_reg}")"
        if wine_quiet regedit /S "${win_reg}"; then
            date -Iseconds > "${REG_MARKER}"
        else
            chaos_log wine "Registry import failed; the game may ask for setup details on first run"
        fi
    else
        chaos_log wine "No .reg file shipped with the game; skipping registry import"
    fi
else
    chaos_log wine "Game registry already imported"
fi

# The shipped AppPath points at wherever the game was installed on the original
# machine. Point it at our in-prefix location instead.
for key in 'HKLM\Software\Stick Man Games\Chaos Overlords\1.0'; do
    if wine_quiet reg query "${key}" /v AppPath; then
        wine_quiet reg add "${key}" /v AppPath /t REG_SZ /d "${GAME_WIN_DIR}" /f || true
        chaos_log wine "AppPath set to ${GAME_WIN_DIR}"
    fi
done

wineserver -w
chaos_log wine "Wine initialization complete"
