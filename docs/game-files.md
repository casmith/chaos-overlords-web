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
