# Game files

Chaos Overlords is copyrighted software. Its files must never be committed to
this repository or baked into the Docker image, so you supply your own copy and
the container mounts it read-only at `/game`.

`GAME_PATH` in `.env` points at that directory on the host. It defaults to
`./chaos`, which `.gitignore` excludes.

## What it should contain

The contents of an **already-installed** Chaos Overlords directory. A typical
retail install looks like:

```
chaos/
├── Chaos Overlords.exe
├── SMACKW32.DLL
├── chaosreg.reg          # optional but recommended (serial + default prefs)
├── DATA/
│   ├── Gangs
│   ├── ITEMS
│   ├── SITES
│   ├── PX08/  PX16/
│   └── SND00xxx ...
└── HELP/
    ├── Chaos.hlp
    └── CHAOS.CNT
```

The executable filename is auto-detected, so you do not have to rename anything.
If auto-detection picks the wrong file, set `GAME_EXE` explicitly in `.env`:

```
GAME_EXE=/game/Chaos Overlords.exe
```

## The GOG release is the better copy

If you have a choice, use it. Alongside the same data files it ships:

- **`MUSIC/Track02.ogg` … `Track09.ogg`** — the soundtrack, plus its own
  `winmm.dll` and the libvorbis DLLs to play it. A retail rip has no music at
  all, because the original is Red Book CD audio and there is no disc in a
  container. The container detects the shim and enables it automatically; see
  [audio-findings.md](audio-findings.md).
- **No serial number** to import, so `chaosreg.reg` is not needed.
- The manual and readme as PDFs, which do no harm.

Its executable is a different binary from the retail one, but everything the
container does works the same on it: the dialog patch applies to the same three
templates, and its bundled winsock DLLs are not loaded, so multiplayer behaves
identically.

A typical GOG install looks like:

```
chaos/
├── Chaos Overlords.exe
├── winmm.dll                 the CD-audio-to-Ogg shim
├── libogg-0.dll  libvorbis-0.dll  libvorbisfile-3.dll
├── MUSIC/Track02.ogg ... Track09.ogg
├── DATA/  HELP/
└── SMACKW32.DLL
```

Point `GAME_PATH` straight at it — Heroic installs to something like
`~/Games/Heroic/Chaos Overlords`.

## Using a different directory

Point `GAME_PATH` at wherever your files already live:

```
GAME_PATH=/srv/games/chaos-overlords
```

The directory is mounted read-only. Anything the game writes (saves, updated
preferences) is redirected into the per-player `/config` volume instead.

## If you only have an installer

The container assumes installed files. If your copy is an installer only, run
the installer once on a Windows machine or under a local Wine prefix and copy
the resulting directory here. Installer support inside the container
(`/game-installer/setup.exe`) is deliberately not implemented unless testing
shows it is needed -- see SPEC section 41.
