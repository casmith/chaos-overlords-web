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

# --- Keep the intro cinematic out of the way --------------------------------
# Clicking through the intro leaves the game unable to draw any masked sprite
# on the city map for the rest of that process: the sector grid letters, the
# site flag and the gang circles all silently stop being drawn, while the map
# tiles, the grid lines and every panel still render. The game state is fine --
# Gangs In Sector lists gangs the map shows nothing for -- so it reads as
# "the icons are broken" rather than "the video ended badly".
#
# Reproduced by starting the same game twice: letting the intro finish gives
# 548 green pixels in the label strip and the gang rings on the map; clicking
# through it gives 0 and 0. Restarting the game process restores them, so it is
# state inside the process rather than anything on disk. Nobody watches a
# 137-second cinematic every time they open a session, so everybody hits this.
#
# The game has no no-intro switch, so take the films away: DATA becomes a real
# directory of links with MVINTRO and MVLOGOS left out, and the game goes
# straight to its title screen. That also cuts session start from about 150
# seconds to 30. Set GAME_INTRO=true to link them back and watch it.
data_dir="${GAME_UNIX_DIR}/DATA"
if [ "${GAME_INTRO}" = "true" ]; then
    if [ -d "${data_dir}" ] && [ ! -L "${data_dir}" ]; then
        rm -rf "${data_dir}"
        ln -sfn "${GAME_DIR}/DATA" "${data_dir}"
        chaos_log wine "Intro enabled: DATA linked whole"
    fi
elif [ -d "${GAME_DIR}/DATA" ]; then
    # Replace the single DATA symlink with a directory of per-entry links.
    [ -L "${data_dir}" ] && rm -f "${data_dir}"
    mkdir -p "${data_dir}"
    skipped=0
    for entry in "${GAME_DIR}"/DATA/*; do
        [ -e "${entry}" ] || continue
        name="$(basename "${entry}")"
        case "${name}" in
            MVINTRO|MVLOGOS|mvintro|mvlogos) skipped=$((skipped + 1)); continue ;;
        esac
        ln -sfn "${entry}" "${data_dir}/${name}"
    done
    # A previous run with GAME_INTRO=true may have left the films linked.
    for name in MVINTRO MVLOGOS mvintro mvlogos; do
        [ -L "${data_dir}/${name}" ] && rm -f "${data_dir}/${name}"
    done
    chaos_log wine "Intro skipped: ${skipped} film(s) left out of DATA (GAME_INTRO=true to keep them)"
fi

# --- Make the game's modal dialogs visible under Wine -----------------------
# Wine's user32 shows a DialogBoxParam dialog only when the template already
# carries WS_VISIBLE (dlls/user32/dialog.c). Three of this game's templates do
# not -- Host Game, Join Game and one more -- so under Wine the game enters a
# modal message loop over a window that is never mapped. The game stops
# responding and looks hung; multiplayer is unreachable.
#
# The fix is a copy of the executable inside the prefix with WS_VISIBLE set on
# those templates. The mounted game files stay read-only and untouched, and
# SPEC section 3 allows patching to the extent Wine compatibility requires.
patch_game_dialogs() {
    local src rel dst stamp src_id
    src="$1"
    rel="${src#"${GAME_DIR}"/}"
    dst="${GAME_UNIX_DIR}/${rel}"
    stamp="${CONFIG_DIR}/state/dialog-patch"
    src_id="$(stat -c '%s:%Y' "${src}" 2>/dev/null)"

    if [ "${PATCH_DIALOG_VISIBILITY}" != "true" ]; then
        # Turned off after having been on: put the plain symlink back rather
        # than silently keeping a patched copy the operator asked us to drop.
        if [ -f "${stamp}" ]; then
            chaos_log wine "Dialog visibility patch disabled; restoring the unmodified executable"
            rm -f "${dst}" "${stamp}"
            ln -sfn "${src}" "${dst}"
        else
            chaos_log wine "Dialog visibility patch disabled; Host/Join dialogs will not appear"
        fi
        return 0
    fi

    # Already patched from this exact source file: nothing to do.
    if [ -f "${dst}" ] && [ ! -L "${dst}" ] && \
       [ "$(cat "${stamp}" 2>/dev/null)" = "${src_id}" ]; then
        chaos_log wine "Game executable already patched for dialog visibility"
        return 0
    fi

    chaos_log wine "Patching game executable so Wine shows its modal dialogs"
    rm -f "${dst}"
    if python3 /opt/chaos/scripts/patch-dialogs.py "${src}" "${dst}" 2>&1 | sed 's/^/[wine] /'; then
        printf '%s' "${src_id}" > "${stamp}"
    else
        chaos_err wine "Dialog patch failed; falling back to the unmodified executable."
        chaos_err wine "The game will run, but Host Game and Join Game will appear to hang."
        rm -f "${dst}" "${stamp}"
        ln -sfn "${src}" "${dst}"
    fi
}

patch_game_dialogs "${game_exe}"

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
