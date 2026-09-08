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

## They did not, and 4:2:0 chroma is why

H.264 normally stores one colour sample per **2x2 block of pixels** — 4:2:0
chroma subsampling. On camera video nobody notices. On a 1996 UI it is
destructive: a two-pixel red tick on a grey ring has its colour averaged with
the grey around it and stops being red.

That is exactly what was happening. Selkies has a switch for it, defaulted off:

```
--video-fullcolor    Encode H.264 with 4:4:4 chroma rather than 4:2:0
```

```python
# selkies/media_pipeline.py
fullcolor: bool = False
```

Measured against the container's own framebuffer (`xwd`) as ground truth, with
a freshly-hired gang in a sector — RMSE, lower is closer:

| | full screen | the 22x22 marker |
|---|---|---|
| h264enc, 4:2:0 (was) | 0.0498 | 0.0921 |
| **h264enc, 4:4:4 (now)** | **0.0386** | **0.0637** |
| jpeg encoder | 0.0376 | — |

The "just hired this turn" red ring marks go from smeared away to plainly
visible. The full screen improves 23% at the same bitrate.

The JPEG encoder scores the same as 4:4:4 H.264 and was not chosen: it would
send the intro cinematic as a stream of full JPEGs, where H.264 is built for
exactly that.

`--video-fullcolor` is passed as an initial value, not locked. A browser whose
decoder has no 4:4:4 profile turns it off for itself; locking it on would make
that browser fall back to the JPEG encoder instead.

**Cost: about 1.5 points of CPU** while somebody is watching — 12.5% to 14.2%
of a core, against ~4.5% for a session nobody is streaming. Irrelevant next to
[the message-loop fix](cpu-findings.md).

## Things that were ruled out along the way

- **Raising the bitrate does nothing.** 2000 vs 8000 kbps: RMSE 0.0498 vs
  0.0497. The encoder was never bitrate-starved at 640x480; it was throwing the
  colour away before the bitrate mattered.
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

`VIDEO_FULLCOLOR`, default `true`. Set it to `false` to go back to 4:2:0 and
save the CPU, at the cost of the small coloured marks.

If a browser still looks soft, the next levers, in order: `SELKIES_VIDEO_CRF`
(constant quality rather than the bitrate target), then `VIDEO_ENCODER=jpeg`
for a sharp per-tile encode at the cost of bandwidth during the intro.
