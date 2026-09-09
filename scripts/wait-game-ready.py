#!/usr/bin/env python3
"""Hold the container unhealthy until the game is ready to be played.

Starting a new game before the game has finished coming up leaves it unable to
draw any masked sprite on the city map for the rest of that process: the sector
grid letters, the site flag and the gang circles all silently stop being drawn
while the map tiles, the grid lines and every panel still render. The game
itself is fine -- Gangs In Sector lists gangs the map shows nothing for -- so it
reads as "the icons are broken".

Measured, by starting a new game a fixed time after the game process appears:

    after  2s   grid-letter pixels    0    broken
    after  5s   grid-letter pixels    0    broken
    after 10s   grid-letter pixels  548    fine
    after 25s   grid-letter pixels  548    fine

The intro cinematic used to hide this: nobody could reach the menu for two
minutes. Skipping the intro made a session ready in thirty seconds and dropped
players straight into the window where it breaks.

The signal is the screen. Waiting for it to merely stop changing is not enough
-- the display is solid black for the first ten seconds and black is perfectly
stable, which is exactly the too-early window. So this waits for the screen to
have *something on it* and then to hold still:

    lit fraction  0.000 for the first ~11s, then 0.750 at the title screen

No timer to guess at, and it behaves the same whether the intro is playing or
not.
"""
import hashlib
import os
import struct
import subprocess
import sys
import time

FLAG = "/run/chaos/game-ready"
DISPLAY = os.environ.get("DISPLAY", ":0")
# The title screen sits at 0.75; a blank screen at 0.00. Anything well clear of
# black means the game has drawn its first real frame.
MIN_LIT = float(os.environ.get("GAME_READY_MIN_LIT", "0.15"))
STABLE = int(os.environ.get("GAME_READY_STABLE_SECONDS", "5"))
TIMEOUT = int(os.environ.get("GAME_READY_TIMEOUT", "300"))
# A floor, because the screen test alone is not enough: the picture can settle
# while the game is still inside the window where starting a game breaks it.
# The measurements above put the boundary between 5s and 10s after launch, so
# this sits well past it. The screen test is what copes with a slow host; this
# is what copes with a screen that looks finished before the game is.
MIN_SECONDS = int(os.environ.get("GAME_READY_MIN_SECONDS", "20"))


def screen():
    """(md5 of the framebuffer, fraction of it that is not near-black)."""
    raw = subprocess.run(["xwd", "-root", "-silent", "-display", DISPLAY],
                         capture_output=True).stdout
    if len(raw) < 100:
        return None, 0.0
    f = struct.unpack(">25I", raw[:100])
    hdr, w, h, bpl, ncol = f[0], f[4], f[5], f[11], f[19]
    off = hdr + ncol * 12
    lit = tot = 0
    # Every fourth row and every fourth pixel: enough to tell a drawn screen
    # from a blank one, cheap enough to run once a second forever.
    for y in range(0, h, 4):
        row = raw[off + y * bpl: off + y * bpl + w * 4]
        for x in range(0, len(row) - 3, 16):
            tot += 1
            if row[x] > 40 or row[x + 1] > 40 or row[x + 2] > 40:
                lit += 1
    return hashlib.md5(raw).hexdigest(), (lit / tot if tot else 0.0)


def main() -> int:
    try:
        os.unlink(FLAG)
    except FileNotFoundError:
        pass

    started = time.time()
    previous, same = None, 0
    while time.time() - started < TIMEOUT:
        digest, lit = screen()
        if digest and lit >= MIN_LIT and digest == previous:
            same += 1
        else:
            same = 0
        previous = digest
        if same >= STABLE and time.time() - started >= MIN_SECONDS:
            open(FLAG, "w").close()
            print(f"[game] Ready to play after {int(time.time() - started)}s",
                  flush=True)
            return 0
        time.sleep(1)

    # Never leave a session unreachable because this could not make up its mind.
    # A screen that keeps changing is far more likely to be something we have
    # not seen than a game that will never settle.
    open(FLAG, "w").close()
    print(f"[game] Screen never settled in {TIMEOUT}s; letting players in anyway",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
