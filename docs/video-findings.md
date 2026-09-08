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

### The third wrong fix: JPEG at quality 90

Quality 90 fixed the artefacts (Selkies' default is 40, which is what any
*moving* region gets, and the blinking sector selector means the area around a
gang marker never settles enough to earn the paint-over). It shipped, and a
remote player reported audio stuttering.

Audio and video share one WebSocket, so a video backlog stalls the audio behind
it. Measured on an idle board over a shaped 5 Mbit link, with sound playing:

| | audio gap p95 | worst gap | gaps >100 ms |
|---|---|---|---|
| h264enc | 10 ms | 78 ms | 0 |
| jpeg q90 | 40 ms | **307 ms** | 2 |

On localhost both are clean, which is why it was not caught: every earlier test
ran over a link with no constraint. **A media change has to be tested on a
constrained link, not just a fast one.**

### Why JPEG is so expensive here, and why it cannot be tuned down

On an idle board only **200–700 pixels change per frame**, and the stream still
sends ~300 KB/s. Profiling the wire: 255 video frames in 15 s (~17/s), each
**16–64 KB**, and 100% of the bytes. A full 640x480 board as one JPEG is 169 KB
at q90, so these are not full frames — they are pixelflux's **full-width
horizontal stripes covering every damaged row**. This game blinks a "WAIT"
indicator at y 26–43 and a sector selector lower down, so the dirty band spans
y 26–196, about a third of the screen, and 640x170 at q90 is ≈59 KB. That is
exactly the frame size on the wire.

A blinking 18x18 cursor therefore costs a third of the screen, because stripes
are full width.

The knobs that should help are hardcoded in Selkies and make no difference:

```python
cs.paint_over_trigger_frames = 15
cs.damage_block_threshold = 10
cs.damage_block_duration = 20
```

Patched live to 500 / 1 and remeasured: 3.66 against 3.63 Mbit/s. Capture-side
scaling — which would be the elegant fix, upscaling 2x before encoding so 4:2:0
chroma cannot erase a two-pixel mark — is `self.scale = 1.0`, hardcoded in
`media_pipeline.py` and not a setting.

That leaves quality and frame rate, both linear and neither close:

| idle board | Mbit/s |
|---|---|
| h264enc | **0.16** |
| jpeg q90 @ 30 fps | 3.63 |
| jpeg q60 @ 30 fps | 1.28 |
| jpeg q90 @ 5 fps | 1.82 |

So JPEG cannot be made cheap in this build. The default is `h264enc`.

### What is still broken

Being precise, because "sharp markers" was too loose a phrase and hid this:

- On **Chrome**, h264 shows the gang circle; what it loses is the fine detail —
  the red ring ticks that mean "hired this turn", and the crispness of the
  circle's edge. That is the 4:2:0 chroma loss, and it is what the measurements
  in this document are about.
- On **Brave**, the operator reports the markers do not appear **at all** on
  h264, which is a different and worse failure than chroma loss.
- On **Firefox**, the operator reports h264 does not work at all.

Neither of those last two has been reproduced here: a real Firefox on an Xvfb
display, on h264 4:2:0, rendered the board and the marker correctly. So
something in the real deployment differs from that test — the likely suspects
are the path through nginx and the Cloudflare tunnel (every local test went
straight to the container), the browsers' own decoder configuration, and Brave's
shields. **That is the open thread, and it is a browser/transport problem rather
than an encoder-quality one.**

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
