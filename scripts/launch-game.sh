#!/bin/bash
# Stage 3: run Chaos Overlords under Wine and keep it running.
#
# Restart policy (SPEC section 27): a game that exits is relaunched after a
# short delay, with capped exponential backoff so a game that cannot start does
# not spin. After GAME_MAX_RESTARTS consecutive fast failures we stop trying but
# keep the container (and its desktop) alive so the error stays visible in the
# browser; `chaos-relaunch` resumes.
set -uo pipefail
# shellcheck source=/dev/null
. /usr/local/bin/chaos-env

export DISPLAY
RELAUNCH_FLAG="/run/chaos/relaunch"

game_exe="$(cat /run/chaos/game_exe 2>/dev/null || chaos_find_game_exe)"
if [ -z "${game_exe}" ] || [ ! -f "${game_exe}" ]; then
    chaos_err game "Game executable not found (GAME_EXE=${GAME_EXE:-unset})"
    exit 1
fi

# Translate /game/Foo/Bar.exe into C:\games\Chaos\Foo\Bar.exe.
rel="${game_exe#"${GAME_DIR}"/}"
win_exe="${GAME_WIN_DIR}\\${rel//\//\\}"

# Stop the message loop from spinning a core. Scoped to the game and the Wine
# processes it starts rather than set container-wide, so nothing else inherits
# it. ld.so expands $LIB per process, which is why the path is single-quoted --
# the 32-bit game and the 64-bit wineserver each get the right object.
YIELD_SHIM='/usr/local/$LIB/yieldsleep.so'
if [ "${WINE_YIELD_SLEEP_US}" != "0" ] && [ -e /usr/local/lib/i386-linux-gnu/yieldsleep.so ]; then
    export LD_PRELOAD="${YIELD_SHIM}${LD_PRELOAD:+:${LD_PRELOAD}}"
    chaos_log game "Yield shim active: sleeping ${WINE_YIELD_SLEEP_US}us instead of spinning"
else
    chaos_log game "Yield shim disabled; expect the game to use a full CPU core"
fi

chaos_log game "Executable: ${game_exe}"
chaos_log game "Windows path: ${win_exe}"
chaos_log game "Working directory: ${GAME_WIN_DIR}"

cd "${GAME_UNIX_DIR}" || {
    chaos_err game "Game directory ${GAME_UNIX_DIR} is missing; Wine init must run first"
    exit 1
}

shutdown_requested=false
game_pid=""

on_term() {
    shutdown_requested=true
    chaos_log game "Shutdown requested; stopping the game"
    if [ -n "${game_pid}" ]; then
        kill -TERM "${game_pid}" 2>/dev/null
        # Let the game close on its own so Wine flushes saves and the registry.
        for _ in $(seq 1 40); do
            kill -0 "${game_pid}" 2>/dev/null || break
            sleep 0.25
        done
    fi
    # Backstop: bring down wineserver and everything still attached to it.
    wineserver -k 2>/dev/null
}
trap on_term TERM INT

wait_for_relaunch() {
    chaos_log game "Giving up on automatic restart. The desktop stays up so the"
    chaos_log game "error remains visible. Run 'docker exec <container> chaos-relaunch' to retry."
    rm -f "${RELAUNCH_FLAG}"
    while [ "${shutdown_requested}" = "false" ]; do
        if [ -f "${RELAUNCH_FLAG}" ]; then
            rm -f "${RELAUNCH_FLAG}"
            chaos_log game "Relaunch requested"
            return 0
        fi
        sleep 2
    done
    return 1
}

failures=0
while [ "${shutdown_requested}" = "false" ]; do
    started=$(date +%s)

    if [ "${WINE_VIRTUAL_DESKTOP}" = "true" ]; then
        chaos_log game "Launching Chaos Overlords in a ${DISPLAY_WIDTH}x${DISPLAY_HEIGHT} Wine desktop"
        wine explorer "/desktop=ChaosOverlords,${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" \
            "${win_exe}" ${GAME_ARGS:-} &
    else
        chaos_log game "Launching Chaos Overlords (no Wine virtual desktop)"
        wine "${win_exe}" ${GAME_ARGS:-} &
    fi
    game_pid=$!

    wait "${game_pid}"
    status=$?
    game_pid=""
    ran_for=$(( $(date +%s) - started ))

    if [ "${shutdown_requested}" = "true" ]; then
        break
    fi

    chaos_log game "Game exited with status ${status} after ${ran_for}s"

    # A session that lasted a while is a normal quit, not a startup failure.
    if [ "${ran_for}" -ge 60 ]; then
        failures=0
    else
        failures=$((failures + 1))
    fi

    if [ "${failures}" -ge "${GAME_MAX_RESTARTS}" ]; then
        wait_for_relaunch || break
        failures=0
        continue
    fi

    # Capped exponential backoff: 2, 4, 8, 16, 30, 30...
    delay="${GAME_RESTART_DELAY}"
    if [ "${failures}" -gt 0 ]; then
        delay=$(( GAME_RESTART_DELAY * (1 << (failures - 1)) ))
        [ "${delay}" -gt 30 ] && delay=30
    fi
    chaos_log game "Restarting in ${delay}s (consecutive failures: ${failures})"
    sleep "${delay}" &
    wait $!
done

chaos_log game "Game supervisor exiting"
wineserver -w 2>/dev/null
exit 0
