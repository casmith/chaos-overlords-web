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
