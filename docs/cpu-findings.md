# Why every session used a whole CPU core

Six sessions on a four-core VM pinned six cores, with nobody playing. This is
what was actually happening, and what fixes it.

## The measurement

A fresh session, sitting untouched on the title screen, held steady at 103% of
a core. Per-process sampling inside the container put essentially all of it in
one place:

```
    tid name                 user%    sys%
    532 Chaos Overlords       31.0    68.6
    572 audio_client_ma        0.2     0.4
        TOTAL                 31.8    69.4
```

Not Selkies, not the encoder, not PulseAudio — the game, and specifically its
main thread, two thirds of it in the kernel. `strace -c` on that thread for 15
seconds says why:

```
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 47.09    1.143002           2    391248    391248 recvmsg
 33.84    0.821378           2    315583           getrusage
 18.42    0.447151           2    157792           sched_yield
  0.52    0.012679          24       527           writev
------ ----------- ----------- --------- --------- ----------------
100.00    2.427219           2    866031    391248 total
```

866,000 syscalls in 15 seconds. Every one of the 391,248 `recvmsg` calls
returned an error.

The ratio gives the loop away: one `sched_yield` for every two `getrusage`,
which is exactly Wine's `NtYieldExecution` —

```c
/* dlls/ntdll/unix/sync.c */
NTSTATUS WINAPI NtYieldExecution(void)
{
    getrusage( RUSAGE_THREAD, &u1 );
    sched_yield();
    getrusage( RUSAGE_THREAD, &u2 );
    /* ...report NO_YIELD_PERFORMED if the switch counts did not move */
}
```

So the game's message loop is the 1996 idiom: peek for a message, and if there
is none, yield and peek again. On a 1996 cooperatively-scheduled Windows that
was the polite thing to do. On an idle Linux container `sched_yield` returns
immediately — there is nothing else runnable — so the loop runs as fast as the
kernel will let it: **10,500 iterations a second, none of them doing anything**.

The `recvmsg` errors are the peek itself: Wine checking its side of the
wineserver socket, finding nothing, every time round.

## The fix

`src/yieldsleep.c` — an `LD_PRELOAD` shim, about forty lines, that intercepts
`sched_yield` on the game's main thread and sleeps 500 microseconds instead:

```c
int sched_yield(void)
{
    if (sleep_ns <= 0 || (pid_t)syscall(SYS_gettid) != main_tid)
        return syscall(SYS_sched_yield);
    nanosleep(&(struct timespec){ 0, sleep_ns }, NULL);
    return 0;
}
```

The main-thread test matters. Wine's audio and wined3d threads have their own
reasons to yield; the target is one specific spinning message pump, not the
process.

It is built for both word sizes and installed so `ld.so` picks the right one
per process:

```
LD_PRELOAD=/usr/local/$LIB/yieldsleep.so
```

`$LIB` expands to `lib/i386-linux-gnu` for the 32-bit game and
`lib/x86_64-linux-gnu` for the 64-bit wineserver, so neither logs a preload
error. `launch-game.sh` sets it around the game only, not container-wide.

## What it costs

Measured on the same container, GOG copy, Wine 10, before and after:

| | spinning | with the shim |
|---|---|---|
| container CPU, title screen | 103% | **4.6%** |
| container CPU, in a started game | 103% | **4.4%** |
| click to repaint | min 26 ms, median 114 ms | min 26 ms, median 115 ms |
| intro cinematic, launch to menu | 137 s | 140 s |
| loop iterations/second | 10,500 | 1,400 |
| syscalls in 15 s | 866,000 | 165,000 |

Input latency is unchanged — 1,400 polls a second is a 0.7 ms window, and the
stream itself only runs at 30 fps. The intro taking 140 seconds instead of 137
is measurement noise on a 90-second video plus a menu, and it settles at the
same place: **the game's clock is not slowed**.

The one visible cost: during the intro cinematic the game renders about half as
many distinct frames (measured by hashing the root window: 191 distinct frames
in 20 s spinning, 93 with the shim). The video is choppier. It is the same
length, the audio is continuous, and nothing in the actual game animates like
that — Chaos Overlords is turn-based and its screens are static.

An adaptive version was tried, passing the yield straight through whenever the
loop had done real work since the last one, on the theory that video frames
would be the busy iterations. It made no difference: the loop never spends more
than ~100 µs between yields even mid-cinematic, so the video is not being drawn
from it. The complexity bought nothing and was dropped.

## Tuning

`WINE_YIELD_SLEEP_US`, default `500`.

- `0` disables the shim entirely and restores the original spin. The game logs
  which mode it is in at startup, so `docker logs` says plainly whether a
  session is spinning.
- Larger values save very little more — the floor is the loop's own work, and
  50 µs, 100 µs, 250 µs and 500 µs all landed between 4% and 10% — while adding
  latency. There is no reason to go above a millisecond or two.

## Sizing a host

A session now idles at roughly **0.05 of a core** rather than one whole core,
and there is no sustained load at all in normal play: the peaks are the intro
cinematic and the video encoder when the screen changes.

`SESSION_CPU_LIMIT` still exists and still applies. It is a ceiling, not a
reservation, so leaving it at 2 costs nothing and caps the damage if a session
ever does start spinning — a game that hits the old behaviour with the shim
disabled takes 2 cores instead of every core it can reach.
