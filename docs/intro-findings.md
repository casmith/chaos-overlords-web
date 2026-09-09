# Clicking through the intro breaks every map sprite

Reported as "the gang markers aren't appearing", and it is not the markers.

## The symptom

On the city map, these stop being drawn:

- the sector grid letters (A–H) and numbers (1–8)
- the site flag on your starting sector
- the gang circles — the icon that says your gangs are in a sector

While these keep drawing perfectly:

- the map tiles and the sector colour tints
- the green grid *lines*
- every panel, every piece of text, the overlord bar, gang portraits

The game state is untouched. **Gangs In Sector** for a sector lists two gangs
that the map shows nothing for, the turn resolves normally, hiring works. So it
reads as "the icons are broken" rather than "something went wrong earlier".

## What it actually is

Everything missing is a small **masked sprite blitted onto the map**. Everything
surviving is an opaque blit or text. One drawing path stops working, and it
stops working for the life of the game process.

The trigger is the **intro cinematic**. Start the same game twice:

| | grid-letter pixels | gang rings on map |
|---|---|---|
| let the intro play to the end | 548 | 2 |
| click through the intro | **0** | **0** |

Killing just the game process inside a broken session — same container, same
Wine prefix, same volume, same X server — brings all of it back immediately. So
it is state inside the process, not the prefix, not the assets, and not the
stream: it is absent from the container's own framebuffer before anything is
encoded.

Nobody watches a 137-second cinematic every time they open a session, so in
practice everybody hits this on every session but the first.

## The fix

The executable has no no-intro switch, so the films are simply not put where the
game can find them. `init-wine.sh` normally symlinks `DATA` into the prefix
whole; with `GAME_INTRO=false` (the default) it makes `DATA` a real directory of
per-entry links and leaves out `MVINTRO` and `MVLOGOS`. The game finds no intro,
goes straight to its title screen, and never enters the state that breaks.

It is also much faster: a session is ready in about **30 seconds instead of
150**, because the intro was most of the wait.

`GAME_INTRO=true` links the films back for anyone who wants to watch it — with
the caveat that clicking through it will break that session's map sprites until
the game restarts.

## The intro was hiding a race, not causing one

Skipping the intro did not end this. It came back, and the reason is worse than
the original: **starting a new game before the game has finished coming up
breaks the sprites the same way.** Measured, by starting a game a fixed time
after the game process appears:

| new game started | grid-letter pixels | |
|---|---|---|
| after 2s | 0 | broken |
| after 5s | 0 | broken |
| after 10s | 548 | fine |
| after 25s | 548 | fine |

So clicking through the intro was never the real trigger — it was one way of
arriving at the menu too early. The intro was a two-minute wall that made the
race impossible to lose. Taking it away cut session start from 150 seconds to
30 and dropped players straight into the window where it breaks.

The fix is `scripts/wait-game-ready.py`: the container stays **unhealthy**, and
so the manager will not hand the session to a player, until the game has
settled. Two conditions, because either alone is not enough:

- **Something is drawn.** Waiting for the screen to merely stop changing fires
  instantly: the display is solid black for the first ten seconds and black is
  perfectly stable. Measured lit fraction is 0.000 until ~11s, then 0.750 at
  the title screen.
- **At least 20 seconds since launch.** The screen can settle while the game is
  still inside the bad window — an early build reported ready at 7s and the very
  next game was broken. The measured boundary is between 5s and 10s, so the
  floor sits well past it. The screen test is what copes with a slow host; the
  floor is what copes with a screen that looks finished before the game is.

A session now reports healthy at about 32 seconds instead of 7. Verified by
starting a game at the very instant the container reports healthy, three times:
548 grid-letter pixels and gang rings present every time, against 0 before.

If it never settles, the flag is set anyway after `GAME_READY_TIMEOUT` (300s) --
a session that cannot be reached at all is worse than one that might have a
drawing bug.

## What this cost, and the lesson

This was the first thing reported and the last thing found. The reason is a
testing artefact: every automated check waited for the intro to finish, because
a helper polled until the screen stopped changing before driving the menus. That
helper made every test take the one path a real player never takes, and it hid
the bug perfectly for as long as the tests were the only thing looking.

Two full days of work went into the video encoder — 4:4:4 chroma, the JPEG
encoder, bitrates, chroma subsampling of the marker's red ticks — on the theory
that the icons were being *degraded*. They were never being drawn at all. The
operator said so repeatedly, including "the gang is present, the icon is
absent", and was right every time.

**A harness that always takes the same path is not evidence about the paths it
never takes.** Where a human would click past something, the tests should click
past it too.
