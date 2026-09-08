# Video findings: why small details vanished on the way to the browser

Reported as "clicking and dragging doesn't work in all the browsers and there
seems to be some refresh issues", then narrowed by the player to "the gang
markers aren't appearing".

Three separate things were true. Only one was a bug.

## Dragging works

Verified twice, because it was the first thing suspected.

Inside the container with `xdotool`: press on a gang portrait in the hire pool,
move, release over a sector. The portrait follows the cursor mid-drag, the card
is stamped **HIRED**, and **Gangs In Sector** for that sector then lists two
gangs where it listed one.

Through the browser, with real `Input.dispatchMouseEvent` events dispatched at
the page — the same path a player's mouse takes: same result, a second gang
stamped HIRED.

So the drag was never the problem. It looked like one because nothing visible
happened afterwards.

## The gang marker is the circle you were already looking at

From the manual, printed page 20, under **City View**:

> On the map you will see various icons in the sectors:
> - small circle with a **green middle** = your gang(s) are in that sector
> - small circle with a **red middle** = your gang(s) are in that sector and
>   they detect enemy gang(s) there also
> - small circle with a **green/red middle with a white question mark** = as
>   above, except your gang(s) in that sector have not all been given commands
> - any small circle **with red marks on the circle** = you just hired a gang
>   into that sector
> - small circle with a **black middle & red marks** = you hired a gang into
>   your sector that has none of your gangs in it
> - **Site icons** = located on the **left side of the sector**

So the green circle with the white `?` **is** the gang marker — it means "your
gangs are here and you have not given all of them orders", which is the same
thing the *IDLE GANG DETECTED* warning says when you end the turn. The white
flag on the left of the sector is a Site icon, not a gang.

There is no display option that hides map icons; the Options menu is Thousands
of Colors, Music, Sound Effects, Base Statistics, Detailed Combat, Slide Panels
and Warn If Idle Gangs, and that is all of it.

Which leaves one real question: the state is encoded in **marks a couple of
pixels across**, in colour — red ring marks, red versus green middle. Did those
survive the stream?

## They did not, and chroma subsampling is why

H.264 normally stores one colour sample per **2x2 block of pixels** — 4:2:0
chroma subsampling. On camera video nobody notices. On a 1996 UI it is
destructive: a two-pixel red tick on a grey ring has its colour averaged with
the grey around it and stops being red.

### The wrong fix: 4:4:4 H.264

Selkies has a switch that makes H.264 keep full chroma, defaulted off:

```
--video-fullcolor    Encode H.264 with 4:4:4 chroma rather than 4:2:0
```

It works, and it broke the deployment. Its documentation says a client whose
decoder has no 4:4:4 profile "turns it off for itself"; **it does not.** Firefox
refuses the session outright, on a black page:

> Error: This session streams video in a format this browser cannot decode.
> Full color (4:4:4) needs a browser whose decoder has that profile.

Brave the same. Chrome has the profile and is perfectly happy — which is how
this reaches you: not as "the stream is broken" but as "it only breaks in some
browsers", which sounds like anything but the encoder.

I shipped this on the strength of the help text and a measurement taken in
Chrome. The lesson is the narrow one: **a change to the wire format has to be
tested in more than one browser**, because the one you automate is the one with
the best codec support.

### The second wrong fix: the JPEG encoder

JPEG needs no H.264 decoder in the browser at all, so it cannot fail the way
4:4:4 does, and it has no chroma subsampling to lose the marks to. Measured on
a **settled** screen it was the best of the three:

| | full screen | the marker | CPU idle | intro cinematic |
|---|---|---|---|---|
| h264enc 4:2:0 | 0.0498 | 0.0921 | 13.0% | 1.17 Mbit/s |
| h264enc 4:4:4 | 0.0386 | 0.0637 | 14.2% | — |
| jpeg | 0.0376 | 0.0465 | 8.0% | 1.15 Mbit/s |

(RMSE, lower is closer.) It shipped, and it looked awful in play — blocky and
artefacted in a way none of those numbers predict.

The reason is in Selkies' defaults:

```python
{"name": "jpeg_quality",            "meta": {"default_value": 40}}
{"name": "paint_over_jpeg_quality", "meta": {"default_value": 90}}
```

**Quality 40 is what you get while anything is moving.** Quality 90 is the
"paint-over" a region settles to once it has been still for a moment. Every
measurement above was taken on a static board, so every one of them measured
the paint-over and none of them measured playing the game. A metric taken at
rest cannot see an artefact that only exists in motion.

### What shipped: JPEG at quality 90

The artefacts were the default, not the encoder. `jpeg_quality` starts at 40 and
that is what anything **moving** gets; 90 is only the paint-over a region
settles to. And the selected sector's box **blinks**, so the area around a gang
marker never settles and never gets the paint-over — the one part of the screen
that had to look right was the one part guaranteed to stay at quality 40.

So the encoder is `jpeg` with the quality passed explicitly rather than left to
Selkies:

```
--encoder=jpeg --jpeg-quality=90 --paint-over-jpeg-quality=95
```

Confirmed on a player's own screen, in Firefox and Brave, in play, before it
became the default. Verified on the wire too, since no endpoint reports the
resolved value: at quality 5 the stream is 0.64 Mbit/s and at 95 it is 3.40,
so the setting genuinely reaches the encoder.

### The cost: bandwidth, and it is not small

| | idle board | intro cinematic | CPU with a client |
|---|---|---|---|
| h264enc 4:2:0 | 0.18 Mbit/s | 1.12 Mbit/s | 13.0% |
| **jpeg q90/95** | **3.68 Mbit/s** | 2.32 Mbit/s | 11.0% |

An idle board costs **twenty times** more than H.264, and more than the intro
cinematic does. That is not a mistake in the table: it is the blinking sector
selector. Every blink re-sends those tiles at quality 90, thirty times a second,
where H.264 codes a small periodic change almost for free. On the title screen,
where nothing blinks, JPEG costs 0.21 Mbit/s — the same as H.264.

Lowering the capture rate helps less than you would hope, because the cost is
per blink rather than per frame:

| `VIDEO_FPS` | 30 | 15 | 10 | 5 |
|---|---|---|---|---|
| idle board | 3.68 | 2.81 | 2.81 | 1.82 Mbit/s |

**Budget about 3.7 Mbit/s of upstream per live session.** At `MAX_SESSIONS=6`
that is roughly 22 Mbit/s, which is worth checking against a home uplink before
a games night. `JPEG_QUALITY` is the lever if it needs to come down — but lower
it by looking at the game, not at this table, because the whole reason this
setting exists is that the numbers did not predict what a player saw.

## Things that were ruled out along the way

- **Raising the bitrate does nothing.** 2000 vs 8000 kbps: RMSE 0.0498 vs
  0.0497. The encoder was never bitrate-starved at 640x480; it was throwing the
  colour away before the bitrate mattered.
- **Menus leave a black hole.** Closing one adds ~16,000 black pixels to the map
  area, and the game does not repaint on expose — the next click on the map
  repairs it (69,538 black pixels back down to 53,495). X backing store (`+bs`),
  turning off the Wine virtual desktop and `win98` vs `winxp` all make no
  difference, so nothing is shipped for it; it is cosmetic and self-healing.
- **The screen is not stale.** `xrefresh` on the live display, forcing every
  window to repaint, changes 223 pixels out of 307,200 — the game really is
  drawing what you see.
- **Not a colour-depth problem.** The game ships parallel `DATA/PX08` and
  `DATA/PX16` sprite sets for 256-colour and high-colour displays, which is
  suggestive, but markers render correctly on the 24-bit display we run.
- **The sector selector blinks.** The white box around the selected sector
  flashes, by design. It makes any before/after pixel comparison that includes
  a sector border bounce between two answers, and it is worth knowing about
  before concluding the stream is unstable.

## Tuning

`VIDEO_ENCODER`, default `jpeg`, with `JPEG_QUALITY` (90) and
`JPEG_PAINT_OVER_QUALITY` (95). `h264enc` and `h264enc-striped` are the
alternatives: far cheaper on an idle board (0.18 against 3.68 Mbit/s) and unable
to carry the game's small coloured marks.

`VIDEO_FULLCOLOR`, default `false`, H.264 only. Do not turn it on unless every
player is on Chrome — see above.

If the picture still looks soft, `JPEG_QUALITY` is the lever; if the bandwidth
above is too much for the uplink, it is the same lever in the other direction.
Judge either by looking at the game in a browser, not by a number.
