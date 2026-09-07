"""Per-player accounts.

A name used to be a filing label: everyone shared one invite password, so
anyone could type someone else's name and see their session card, password
included. Names are now claimed.

The first time a name is used, it is claimed with the shared invite password and
the player immediately chooses their own. From then on the invite password no
longer opens that name -- only the password they set does. They can change it
whenever they like, and the admin can release a name if someone forgets.

This is deliberately small: a JSON file of salted PBKDF2 hashes, no e-mail, no
reset flow, no sessions table. The threat being addressed is a friend idly
opening someone else's game, not a determined attacker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("accounts")

ITERATIONS = 200_000
MIN_PASSWORD = 6


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


class Accounts:
    def __init__(self, state_dir: str):
        self.path = Path(state_dir) / "accounts.json"
        self.data: dict[str, dict] = {}
        self._load()

    # --- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.data = json.loads(self.path.read_text()).get("accounts", {})
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); starting with no accounts", self.path, exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"accounts": self.data}, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # --- queries -------------------------------------------------------------

    def exists(self, name: str) -> bool:
        return name in self.data

    def names(self) -> list[str]:
        return sorted(self.data)

    def verify(self, name: str, password: str) -> bool:
        acct = self.data.get(name)
        if not acct:
            return False
        expected = _hash(password, bytes.fromhex(acct["salt"]))
        return hmac.compare_digest(expected, acct["hash"])

    # --- changes -------------------------------------------------------------

    def set_password(self, name: str, password: str) -> None:
        """Claim a name, or change an existing account's password."""
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        salt = secrets.token_bytes(16)
        self.data[name] = {
            "salt": salt.hex(),
            "hash": _hash(password, salt),
            "claimed": self.data.get(name, {}).get("claimed", time.time()),
            "updated": time.time(),
        }
        self._save()
        log.info("password set for account %r", name)

    def release(self, name: str) -> bool:
        """Un-claim a name so it can be claimed again with the invite password.

        For the case where someone forgets their password. Their sessions are
        untouched -- they are filed under the name, and whoever re-claims it
        gets them back.
        """
        if self.data.pop(name, None) is None:
            return False
        self._save()
        log.info("released account %r", name)
        return True
