"""Session lifecycle: create, resume, stop and reap Chaos Overlords containers.

State lives in Docker (the containers and volumes themselves) plus a small JSON
file for the parts Docker cannot hold -- chiefly the generated password, which
has to be shown again when a player comes back to the landing page.

The Docker SDK is synchronous, so every call into it goes through a thread
executor; container creation takes long enough to stall the event loop and stop
the proxy mid-stream otherwise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import string
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import docker
from docker.errors import NotFound, APIError

log = logging.getLogger("sessions")

LABEL_SESSION = "chaos.session.id"
LABEL_MANAGED = "chaos.managed"

# Ambiguous characters are left out: these get read off a screen and typed by
# hand, often dictated to someone else.
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def make_password(length: int = 12) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def make_id(length: int = 6) -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(length))


@dataclass
class Session:
    id: str
    password: str
    label: str = ""
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    status: str = "starting"          # starting | ready | stopped
    active_conns: int = 0             # live proxied WebSockets

    @property
    def container_name(self) -> str:
        return f"chaos-session-{self.id}"

    @property
    def volume_name(self) -> str:
        return f"chaos-session-{self.id}-config"

    @property
    def path(self) -> str:
        return f"/s/{self.id}"

    def public(self) -> dict:
        d = asdict(self)
        d["path"] = self.path
        d["idle_seconds"] = int(time.time() - self.last_seen) if not self.active_conns else 0
        return d


class SessionManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sessions: dict[str, Session] = {}
        self.client = docker.from_env()
        self.state_path = Path(cfg["state_dir"]) / "sessions.json"
        self._lock = asyncio.Lock()

    # --- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text())
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); starting with no sessions", self.state_path, exc)
            return
        for d in raw.get("sessions", []):
            d.pop("active_conns", None)          # never meaningful across a restart
            try:
                self.sessions[d["id"]] = Session(**d, active_conns=0)
            except TypeError as exc:
                log.warning("skipping unreadable session record: %s", exc)

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"sessions": [asdict(s) for s in self.sessions.values()]}, indent=2))
        # The file holds session passwords; keep it to the manager's own user.
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    # --- docker helpers (all blocking; call through _run) --------------------

    async def _run(self, fn, *a, **kw):
        return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **kw))

    def _container(self, session: Session):
        try:
            return self.client.containers.get(session.container_name)
        except NotFound:
            return None

    def _create_container(self, session: Session):
        cfg = self.cfg
        env = {
            "WEB_SUBFOLDER": session.path,
            "WEB_PASSWORD": session.password,
            "WEB_USER": cfg["web_user"],
            # TLS terminates at the operator's proxy in front of the manager;
            # manager-to-session traffic stays on the internal Docker network.
            "ENABLE_HTTPS": "false",
            "TZ": cfg["tz"],
        }
        env.update(cfg["session_env"])

        return self.client.containers.run(
            cfg["image"],
            name=session.container_name,
            hostname=session.container_name,
            detach=True,
            environment=env,
            network=cfg["network"],
            volumes={
                cfg["game_path"]: {"bind": "/game", "mode": "ro"},
                session.volume_name: {"bind": "/config", "mode": "rw"},
            },
            labels={LABEL_MANAGED: "true", LABEL_SESSION: session.id},
            # No published ports: a session is reachable only through the
            # manager's proxy, which is what enforces the password.
            ports={},
            security_opt=["no-new-privileges:true"],
            mem_limit=cfg["mem_limit"] or None,
            nano_cpus=int(float(cfg["cpu_limit"]) * 1e9) if cfg["cpu_limit"] else None,
            restart_policy={"Name": "unless-stopped"},
        )

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        self._load()
        await self.reconcile()

    async def reconcile(self) -> None:
        """Make our view match Docker's after a manager restart or a crash."""
        containers = await self._run(
            self.client.containers.list, all=True, filters={"label": f"{LABEL_MANAGED}=true"})
        running = {c.labels.get(LABEL_SESSION): c for c in containers}

        for sid, session in list(self.sessions.items()):
            c = running.get(sid)
            if c is None:
                if session.status != "stopped":
                    log.info("session %s has no container; marking stopped", sid)
                    session.status = "stopped"
            elif c.status != "running":
                session.status = "stopped"
            else:
                session.status = "ready"

        # A container we have no record of is not usable -- we cannot know its
        # password -- so clear it out rather than leave it running forever.
        for sid, c in running.items():
            if sid and sid not in self.sessions:
                log.warning("removing orphaned session container %s", c.name)
                await self._run(c.remove, force=True)
        self._save()

    async def create(self, label: str = "") -> Session:
        async with self._lock:
            if len([s for s in self.sessions.values() if s.status != "stopped"]) >= self.cfg["max_sessions"]:
                raise RuntimeError(
                    f"already at the limit of {self.cfg['max_sessions']} live sessions")
            sid = make_id()
            while sid in self.sessions:
                sid = make_id()
            session = Session(id=sid, password=make_password(), label=label.strip()[:40])
            self.sessions[sid] = session
            self._save()

        try:
            await self._run(self._create_container, session)
        except APIError as exc:
            self.sessions.pop(session.id, None)
            self._save()
            raise RuntimeError(f"could not start the session container: {exc}") from exc

        log.info("created session %s (%s)", session.id, session.label or "unnamed")
        asyncio.create_task(self._await_ready(session))
        return session

    async def resume(self, session: Session) -> None:
        """Recreate a reaped session's container against its existing volume."""
        async with self._lock:
            if self._container(session) is not None:
                return
            session.status = "starting"
            session.last_seen = time.time()
            self._save()
        await self._run(self._create_container, session)
        log.info("resumed session %s", session.id)
        asyncio.create_task(self._await_ready(session))

    async def _await_ready(self, session: Session) -> None:
        """Flip to 'ready' once the container reports healthy."""
        deadline = time.time() + self.cfg["start_timeout"]
        while time.time() < deadline:
            await asyncio.sleep(3)
            c = await self._run(self._container, session)
            if c is None:
                return
            await self._run(c.reload)
            health = (c.attrs.get("State", {}).get("Health") or {}).get("Status")
            if health == "healthy":
                session.status = "ready"
                session.last_seen = time.time()
                self._save()
                log.info("session %s is ready", session.id)
                return
            if c.status != "running":
                log.warning("session %s container exited while starting", session.id)
                session.status = "stopped"
                self._save()
                return
        log.warning("session %s did not become healthy in time", session.id)

    async def stop(self, session: Session, *, remove_volume: bool = False) -> None:
        """Free the compute. The volume (and so the saves) survives by default."""
        c = await self._run(self._container, session)
        if c is not None:
            # Wine needs a moment to flush saves and the registry on the way out.
            await self._run(c.stop, timeout=self.cfg["stop_timeout"])
            await self._run(c.remove, force=True)
        session.status = "stopped"
        session.active_conns = 0
        if remove_volume:
            try:
                v = await self._run(self.client.volumes.get, session.volume_name)
                await self._run(v.remove, force=True)
            except NotFound:
                pass
            self.sessions.pop(session.id, None)
        self._save()
        log.info("stopped session %s%s", session.id, " and removed its data" if remove_volume else "")

    async def delete(self, session: Session) -> None:
        await self.stop(session, remove_volume=True)

    # --- reaper --------------------------------------------------------------

    async def reap_loop(self) -> None:
        idle_after = self.cfg["idle_minutes"] * 60
        retention = self.cfg["retention_hours"] * 3600
        while True:
            await asyncio.sleep(self.cfg["reap_interval"])
            now = time.time()
            try:
                for session in list(self.sessions.values()):
                    if session.active_conns > 0:
                        session.last_seen = now
                        continue
                    idle = now - session.last_seen
                    if session.status in ("ready", "starting") and idle > idle_after:
                        log.info("session %s idle for %ds; stopping", session.id, int(idle))
                        await self.stop(session)
                    elif session.status == "stopped" and retention and idle > retention:
                        log.info("session %s unused for %dh; removing its data",
                                 session.id, int(idle / 3600))
                        await self.delete(session)
            except Exception:                       # noqa: BLE001 - the reaper must not die
                log.exception("reaper iteration failed")

    # --- proxy bookkeeping ---------------------------------------------------

    def touch(self, session: Session) -> None:
        session.last_seen = time.time()

    def conn_opened(self, session: Session) -> None:
        session.active_conns += 1
        session.last_seen = time.time()

    def conn_closed(self, session: Session) -> None:
        session.active_conns = max(0, session.active_conns - 1)
        session.last_seen = time.time()
