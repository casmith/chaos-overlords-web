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

### The right fix: stop needing a video decoder

The `jpeg` encoder has no chroma subsampling to lose the marks to, and needs no
H.264 decoder in the browser at all. Measured against the container's own
framebuffer (`xwd`) as ground truth:

| | full screen | the marker | CPU, static screen | intro cinematic |
|---|---|---|---|---|
| h264enc 4:2:0 | 0.0498 | 0.0921 | 13.0% | 1.17 Mbit/s |
| h264enc 4:4:4 | 0.0386 | 0.0637 | 14.2% | — |
| **jpeg** | **0.0376** | **0.0465** | **8.0%** | **1.15 Mbit/s** |

(RMSE, lower is closer.)

So JPEG is sharper than 4:4:4 H.264, costs a third less CPU than 4:2:0, sends
the same bandwidth even through the intro cinematic, and works in every browser.
The bandwidth is the surprise — the reason it holds up is that this is a 640x480
window where almost nothing changes, and Selkies only sends the tiles that did.
It renders on a canvas with `image-rendering: crisp-edges`, where the H.264 path
uses a `<video>` element the browser is free to smooth.

Verified in a real Firefox, driven on an Xvfb display rather than through an
automation API: the gang marker and its ring are there, and slightly cleaner
than the same session on H.264.

`VIDEO_FULLCOLOR` remains available and defaults to **false**. It only affects
the H.264 encoders. Turn it on only if every player is on Chrome.

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

`VIDEO_ENCODER`, default `jpeg`. `h264enc` and `h264enc-striped` are the
alternatives; they need a working H.264 decoder in every player's browser and
buy nothing here.

`VIDEO_FULLCOLOR`, default `false`, H.264 only. Do not turn it on unless every
player is on Chrome — see above.

If the picture still looks soft, the next lever is `SELKIES_JPEG_QUALITY`
(and `SELKIES_PAINT_OVER_JPEG_QUALITY`, which is what a static screen settles
to).
