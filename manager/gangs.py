"""Session names taken from the game's own gang list.

`ab12cd` is a fine identifier and a poor thing to say out loud. The game ships
90 gang names in DATA/Gangs, which is more than enough for the number of games
anyone will run at once, and they recycle: a name is free again as soon as the
session holding it is deleted.

The list is **read from the operator's own game files at run time**, never
baked into the image. The names are the game's content, and this image is
published publicly; the same reasoning that keeps the .exe and the data files
out of it applies to a list extracted from them. If the file is not there the
manager falls back to random identifiers and says so once, so a deployment
without the game mounted still works.

File format, established by inspection: 90 fixed 156-byte records. The first 32
bytes of each are the name, NUL-terminated and space-padded; the next 90 are
the description in three 30-column lines; the rest is the gang's stats.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("gangs")

RECORD_SIZE = 156
NAME_FIELD = 32


def _slug(name: str) -> str:
    """A gang name as something safe in a URL, a hostname and a container name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load(game_dir: str) -> list[str]:
    """Gang-name slugs from DATA/Gangs, or [] when it cannot be read."""
    path = Path(game_dir) / "DATA" / "Gangs"
    try:
        data = path.read_bytes()
    except OSError as exc:
        log.info("no gang names (%s); sessions will get random identifiers", exc)
        return []

    if not data or len(data) % RECORD_SIZE:
        log.warning("%s is %d bytes, not a whole number of %d-byte records; "
                    "falling back to random identifiers", path, len(data), RECORD_SIZE)
        return []

    names, seen = [], set()
    for i in range(len(data) // RECORD_SIZE):
        field = data[i * RECORD_SIZE : i * RECORD_SIZE + NAME_FIELD]
        raw = field.split(b"\x00")[0].decode("latin-1").strip()
        slug = _slug(raw)
        # A slug has to survive being a DNS label and a Docker name, and two
        # gangs must not collapse onto one session name.
        if slug and slug not in seen and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}[a-z0-9]", slug):
            seen.add(slug)
            names.append(slug)

    if not names:
        log.warning("%s parsed but yielded no usable names; using random identifiers", path)
    else:
        log.info("%d gang names available for sessions", len(names))
    return names
