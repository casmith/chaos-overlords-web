# Audio findings

Phase 3. Game audio reaches the browser — confirmed by a player, not just by
counting bytes. Three things came out of looking at the reported problems.

## The sound effects are 8-bit

Every sound the game ships is the same format:

```
$ file chaos/DATA/SND00500
RIFF (little-endian) data, WAVE audio, Microsoft PCM, 8 bit, mono 22050 Hz
```

8-bit PCM has roughly 48 dB of dynamic range, and quantisation noise sits right
in the audible band. A certain amount of hiss and grit is in the source files
and cannot be removed downstream — this is what the game sounded like in 1996.

What *can* be fixed is the path not adding to it.

## The path was resampling twice (fixed)

As shipped, the chain was:

| stage | rate |
|---|---|
| the game's WAVs | 22050 Hz, 8-bit mono |
| PulseAudio null sink | **44100 Hz** (PulseAudio's default) |
| pcmflux → Opus | **48000 Hz** (Opus always works at 48 kHz) |

So audio was resampled twice — 22.05 → 44.1, then 44.1 → 48 — and both times
with `speex-float-1`, PulseAudio's *lowest* quality resampler, which is the
built-in default.

The sink is now pinned to 48 kHz to match Opus, and the resampler raised:

```
# config/pulseaudio/daemon.conf
default-sample-rate = 48000
resample-method = speex-float-5
```

```
# config/pulseaudio/default.pa
load-module module-null-sink sink_name=chaos-out rate=48000 channels=2 format=s16le
```

That leaves exactly one resample, 22.05 → 48, at a much better quality setting.
The cost is irrelevant: this is one mono voice on an otherwise idle CPU.

**Verified**, by pushing one of the game's own 8-bit WAVs through the live sink
and capturing the monitor:

```
captured 6.7s at 48000 Hz  rms=2253 peak=22996
```

**Not verified**: that this actually fixes the scratchiness a player hears. The
game only emits sound on certain events, and it stayed silent through every
state I could drive it to from a script, so there was no before-and-after to
compare by ear. What was checked is that the change is not a regression — an
identical A/B, old config against new on the same image, behaves the same.

If it still sounds rough, the next things to try, in order: raise
`AUDIO_BITRATE` from 96000 to 128000; try `resample-method = soxr-vhq`; and
capture the monitor during the offending sound with

```bash
docker exec <container> parec --device=chaos-out.monitor --file-format=wav /tmp/c.wav
```

to establish whether the grit is already in the sink (the game, or the
resampler) or is being added afterwards by Opus.

## In-game volume cannot work under Wine

The game's volume sliders do nothing, and they cannot be made to work without
patching Wine.

It controls volume through the **auxiliary audio API**, the Windows 3.1-era
interface that used to drive a sound card's CD-audio input:

```
$ strings "Chaos Overlords.exe" | grep -iE '^aux'
auxGetDevCapsA
auxGetNumDevs
auxGetVolume
auxSetVolume
```

The calls really are made — this is Wine's own trace with the slider moving:

```
trace:winmm:auxGetVolume (0000, 0031FC28) !
trace:winmm:auxSetVolume (0000, 2516555264) !
```

But Wine's `auxSetVolume` needs an `MMDRV_AUX` device to hand the request to:

```c
/* dlls/winmm/winmm.c */
UINT WINAPI auxSetVolume(UINT uDeviceID, DWORD dwVolume)
{
    if ((wmld = MMDRV_Get((HANDLE)(DWORD_PTR)uDeviceID, MMDRV_AUX, TRUE)) == NULL)
        return MMSYSERR_INVALHANDLE;
    return MMDRV_Message(wmld, AUXDM_SETVOLUME, dwVolume, 0L);
}
```

Wine's modern audio drivers register wave and MIDI devices, not aux ones, so
there is no device, the call returns `MMSYSERR_INVALHANDLE`, and the volume is
dropped on the floor. The game has no way to know.

**Use the browser's volume instead.** The Selkies sidebar has an audio control,
and the browser's own per-tab volume works too. Both sit after the game in the
chain, so they do what the in-game slider was meant to do. This is why
`--ui-sidebar-show-audio-settings` is deliberately left enabled.

## The music is CD audio — which the GOG release solves

**Short version: use the GOG copy and you get the soundtrack.** The analysis
below is still why, and still applies to a retail CD rip.

GOG's release ships the soundtrack as `MUSIC/Track02.ogg` … `Track09.ogg`
(Ogg Vorbis, 44.1 kHz stereo, ~45 MB) alongside its own `winmm.dll` and the
`libogg` / `libvorbis` / `libvorbisfile` DLLs. That `winmm.dll` is a shim: it
exports the whole `mci*` API and answers the game's `cdaudio` calls by playing
those Ogg files instead of a disc.

Wine loads its **builtin** winmm unless told otherwise, so the shim would sit
unused next to the game. The container therefore adds `winmm=n,b` to
`WINEDLLOVERRIDES` when it finds a `winmm.dll` in the game directory —
detected from the files, not configured, because a copy that ships one always
wants it and a copy that does not has nothing to override.

Confirmed working: with the GOG copy and no manual configuration, Wine loads
`C:\games\Chaos\WINMM.dll` and `libvorbisfile-3.dll` as **native**, and the
title screen plays ten seconds of continuous audio where the retail copy is
silent. The retail copy is unaffected — it has no `winmm.dll`, so no override is
added.

The GOG executable is a different binary from the retail one, but the dialog
patch applies identically (the same three templates at the same offsets), and it
needs no serial number in the registry.

### In-game music needs one nudge per game

**Music does not start on its own when you begin a game.** It plays over the
intro and on the title screen, then stops when a game starts and is not
restarted. Selecting any value under **Options -> Music** starts it, after which
it keeps playing.

Measured, on a session driven into an actual game:

| moment | audio |
|---|---|
| intro cinematic | continuous |
| title screen, intro finished | 12/12 s, peak 15760 |
| in game, untouched | **0/12 s, peak 0** |
| in game, after Options -> Music -> 5 | 10/10 s, peak 15815 |
| 20 s later, untouched again | 10/10 s |

It is not a volume problem: the setting was already on 5 of 10 and not muted
while it was silent, and re-selecting that same 5 started it. Something about
the transition into a game leaves the track stopped, and any menu selection
re-issues the play.

This was originally written up as "music works", on the strength of captures
that turned out to be the intro cinematic's own soundtrack playing over a
screenshot nobody looked at closely. The intro runs about 90 seconds, which is
long enough to fool a 12-second capture taken shortly after launch. The lesson
is the same one as `localhost`: check what is actually on screen before
believing what the meter says.

Whether the same happens under Windows is untested. The shim is native code
loaded by Wine, so `WINEDEBUG=+mci` cannot see the calls it handles, and there
is no obvious way to make the game re-issue the play from outside.

Its bundled `wsock32.dll`, `mswsock.dll`, `dpwsockx.dll` and `ipxwrapper.dll`
are **not** loaded — Wine uses its builtin winsock — so multiplayer behaves
exactly as measured in [multiplayer-findings.md](multiplayer-findings.md).

## Why a retail rip has no music

With a retail rip, music is not broken and not a bad copy of the game. It is
Red Book audio from the original CD, and the container has no CD drive.

Wine's MCI trace, taken at startup:

```
trace:mci:MCI_Open devType=L"cdaudio" !
trace:mci:MCI_LoadMciDriver Loaded driver (L"CDAUDIO"), type is 516
trace:mci:MCI_Open Failed to open driver (MCI_OPEN_DRIVER) [0000010a], closing
```

The game asks MCI for the `cdaudio` device, the open fails because no such
device exists, and the game carries on without music. That also explains the
`prefsVolumeCD` key in the shipped registry, and why the volume slider is wired
to the aux API — aux *was* the CD-audio volume control.

Making a *retail rip* play music would mean presenting a real CD device to the
container — the tracks are not files on the data track, so no amount of copying
data files produces them. Passing a host drive through with `--device /dev/sr0`
and the disc in it would work; so would emulating one with `cdemu`, which needs
a kernel module and is therefore a host change rather than a container one.

Neither is worth building, because the GOG release makes the whole question moot:
it ships the same tracks as Ogg files and a shim to play them.

## Summary

| Symptom | Cause | Status |
|---|---|---|
| Audio reaches the browser | — | works |
| Scratchy | 8-bit 22 kHz source, plus a double resample at the lowest quality | source is inherent; the double resample is fixed, effect unconfirmed |
| In-game volume does nothing | Game uses the aux API; Wine registers no aux device | cannot be fixed in the container — use the browser's volume |
| No music | Red Book CD audio via MCI, no disc present | **solved by using the GOG copy**, which ships the tracks as Ogg plus a winmm shim; a retail rip still needs the disc |
| No music *in game* | The track is not restarted when a game begins | pick any level under Options -> Music, once per game; it then keeps playing |
