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

import gangs

log = logging.getLogger("sessions")

LABEL_SESSION = "chaos.session.id"
LABEL_MANAGED = "chaos.managed"
# The volume carries everything needed to reach the save it holds. Saves can be
# kept indefinitely, so they must not depend on one JSON file surviving: if the
# manager's state is lost, sessions are rebuilt from the volumes themselves.
LABEL_PASSWORD = "chaos.session.password"
LABEL_LABEL = "chaos.session.label"
LABEL_OWNER = "chaos.session.owner"
LABEL_CREATED = "chaos.session.created"

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
    # Who may see and manage this session on the landing page. Empty means the
    # admin created it; otherwise the name a guest signed in with.
    owner: str = ""
    # URL prefix the container serves under. Empty when the session has a
    # hostname of its own and is served at the root; "/s/<id>" when several
    # sessions share one hostname and are told apart by path. Recorded per
    # session because it is baked into the container's environment, so a
    # running container must keep the value it was started with.
    subfolder: str = ""
    # Seconds anyone has actually been watching, accumulated. Counted from the
    # proxied WebSockets rather than from how long the container has existed:
    # a session left running overnight with nobody in it is not playtime, and a
    # stopped session that gets resumed keeps what it has already earned.
    #
    # `played_since` is when the current stretch began, or 0.0 when nobody is
    # connected. It is folded into `played_seconds` when the last viewer leaves,
    # so the pair is the running total plus an open interval.
    played_seconds: float = 0.0
    played_since: float = 0.0

    @property
    def playtime(self) -> float:
        """Total watched seconds, including any stretch still in progress."""
        if self.played_since:
            return self.played_seconds + max(0.0, time.time() - self.played_since)
        return self.played_seconds

    @property
    def container_name(self) -> str:
        return f"chaos-session-{self.id}"

    @property
    def volume_name(self) -> str:
        return f"chaos-session-{self.id}-config"

    @property
    def path(self) -> str:
        return f"/s/{self.id}"

    def hostname(self, domain: str) -> str:
        return f"{self.id}.{domain}"

    def public(self) -> dict:
        d = asdict(self)
        d["path"] = self.path
        d["idle_seconds"] = int(time.time() - self.last_seen) if not self.active_conns else 0
        d["playtime_seconds"] = int(self.playtime)
        return d


class SessionManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sessions: dict[str, Session] = {}
        # Read from the operator's own game files; empty when they are not
        # mounted, in which case sessions fall back to random identifiers.
        self.gang_names: list[str] = gangs.load(cfg["game_dir"])
        self.client = docker.from_env()
        self.state_path = Path(cfg["state_dir"]) / "sessions.json"
        self._lock = asyncio.Lock()
        # Set to the image name once a pull has failed, so the warning is
        # logged once rather than on every session created.
        self._pull_failed_for = ""

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
            # An open playtime stretch cannot survive a restart either -- we do
            # not know when the manager stopped. The reaper banks the total
            # every tick, so what is dropped here is at most one tick.
            d["played_since"] = 0.0
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

    def _ensure_volume(self, session: Session):
        """Create the session's volume, labelled so it describes itself."""
        try:
            return self.client.volumes.get(session.volume_name)
        except NotFound:
            return self.client.volumes.create(
                name=session.volume_name,
                labels={
                    LABEL_MANAGED: "true",
                    LABEL_SESSION: session.id,
                    LABEL_PASSWORD: session.password,
                    LABEL_LABEL: session.label,
                    LABEL_OWNER: session.owner,
                    LABEL_CREATED: str(int(session.created)),
                },
            )

    def _pull_image(self):
        """Fetch the session image if the tag has moved since we last looked.

        `containers.run` pulls only when the image is absent, so a moving tag
        like :latest pins itself to whatever was pulled first and stays there
        forever. `docker compose pull` does not help: sessions are created by
        this manager at run time, not declared in the compose file, so the
        session image is not one of the images compose knows about. The result
        is a host that has been updated in every visible way and still starts
        every new session on a months-old build.

        Failure is not fatal. A registry that is unreachable, or a private
        image with no credentials here, should mean "carry on with the image
        we have", not "nobody can start a game".
        """
        image = self.cfg["image"]
        if self._pull_failed_for == image:
            return                              # already tried and said so
        try:
            before = self._image_id(image)
            self.client.images.pull(image)
            after = self._image_id(image)
            if before and after and before != after:
                log.info("session image %s updated (%s -> %s)",
                         image, before[7:19], after[7:19])
            elif not before:
                log.info("session image %s pulled", image)
        except (APIError, OSError) as exc:
            # Once per image: a broken registry must not fill the log with one
            # traceback per session created.
            self._pull_failed_for = image
            log.warning("could not refresh %s (%s); using the local copy", image, exc)

    def _image_id(self, image: str) -> str:
        try:
            return self.client.images.get(image).id
        except (NotFound, APIError):
            return ""

    def _create_container(self, session: Session):
        cfg = self.cfg
        self._pull_image()
        self._ensure_volume(session)
        # A session gets its own hostname when a wildcard domain is configured,
        # and shares the manager's hostname otherwise. Fixed here rather than
        # read live, so a config change cannot desync a running container from
        # the prefix it was started with.
        session.subfolder = "" if cfg["session_domain"] else session.path
        env = {
            "WEB_SUBFOLDER": session.subfolder,
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

    def _next_id(self) -> str:
        """A free gang name, or a random identifier when none is available.

        Names recycle: one is free again the moment the session holding it is
        deleted. Stopped sessions keep theirs, because their saves are still
        there and the name is how a player finds them again.
        """
        taken = set(self.sessions)
        free = [n for n in self.gang_names if n not in taken]
        if free:
            return secrets.choice(free)

        # Every gang is spoken for. Rather than fail, number a repeat -- still
        # readable, still unique, and it only happens past 90 live sessions.
        if self.gang_names:
            for suffix in range(2, 1000):
                candidates = [f"{n}-{suffix}" for n in self.gang_names
                              if f"{n}-{suffix}" not in taken]
                if candidates:
                    return secrets.choice(candidates)

        sid = make_id()
        while sid in taken:
            sid = make_id()
        return sid

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

        await self.adopt_orphaned_volumes()
        self._save()

    async def adopt_orphaned_volumes(self) -> None:
        """Rebuild sessions for save volumes we have no record of.

        Saves outlive containers and can be kept indefinitely, so a lost or
        rolled-back state file must not strand them. Everything needed is on the
        volume's own labels.
        """
        try:
            volumes = await self._run(
                self.client.volumes.list, filters={"label": f"{LABEL_MANAGED}=true"})
        except APIError as exc:
            log.warning("could not list session volumes: %s", exc)
            return

        for v in volumes:
            labels = v.attrs.get("Labels") or {}
            sid = labels.get(LABEL_SESSION)
            password = labels.get(LABEL_PASSWORD)
            if not sid or sid in self.sessions:
                continue
            if not password:
                log.warning("volume %s has no recorded password; leaving it alone", v.name)
                continue
            created = float(labels.get(LABEL_CREATED) or time.time())
            self.sessions[sid] = Session(
                id=sid, password=password, label=labels.get(LABEL_LABEL, ""),
                owner=labels.get(LABEL_OWNER, ""),
                created=created, last_seen=created, status="stopped")
            log.info("adopted save volume %s as session %s", v.name, sid)

    async def set_owner(self, session: Session, owner: str) -> None:
        """Hand a session to a player, or take it back to nobody.

        The owner lives in this manager's state file. The session's save volume
        also carries the owner it was created with, as a label, because that is
        what rebuilds sessions when the state file is lost -- and Docker volume
        labels cannot be changed after creation. So a reassignment recorded here
        is not reflected there: if this manager's data volume is ever lost and
        sessions are rebuilt from the save volumes alone, they come back with
        the owner they were *created* with, not the one they were assigned to.
        Worth knowing, not worth a second copy of the truth; the state file and
        the volumes live in the same place and are lost together or not at all.
        """
        async with self._lock:
            session.owner = owner
            if not session.label:
                session.label = owner
            self._save()
        log.info("session %s now belongs to %r", session.id, owner or "nobody")

    def owned_by(self, owner: str) -> list[Session]:
        """Sessions filed under a name; empty for the empty name."""
        return [s for s in self.sessions.values() if owner and s.owner == owner]

    async def create(self, label: str = "", owner: str = "") -> Session:
        async with self._lock:
            if len([s for s in self.sessions.values() if s.status != "stopped"]) >= self.cfg["max_sessions"]:
                raise RuntimeError(
                    f"already at the limit of {self.cfg['max_sessions']} live sessions")
            if owner:
                mine = [s for s in self.sessions.values() if s.owner == owner]
                if len(mine) >= self.cfg["max_sessions_per_guest"]:
                    raise RuntimeError(
                        f"you already have {len(mine)} session(s); "
                        "delete one before starting another")
            sid = self._next_id()
            session = Session(id=sid, password=make_password(),
                              label=label.strip()[:40], owner=owner)
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
        # RETENTION_HOURS=0 keeps saves indefinitely: a stopped session is only
        # ever removed when someone asks for it to be.
        retention = self.cfg["retention_hours"] * 3600
        while True:
            await asyncio.sleep(self.cfg["reap_interval"])
            now = time.time()
            dirty = False
            try:
                for session in list(self.sessions.values()):
                    if session.active_conns > 0:
                        session.last_seen = now
                        # Bank the playtime so far. Without this a three-hour
                        # sitting would be lost entirely if the manager
                        # restarted before the last viewer left; folding here
                        # costs at most one tick.
                        if session.played_since:
                            session.played_seconds += max(0.0, now - session.played_since)
                            session.played_since = now
                            dirty = True
                        continue
                    idle = now - session.last_seen
                    if session.status in ("ready", "starting") and idle > idle_after:
                        log.info("session %s idle for %ds; stopping", session.id, int(idle))
                        await self.stop(session)
                    elif session.status == "stopped" and retention and idle > retention:
                        log.info("session %s unused for %dh; removing its data",
                                 session.id, int(idle / 3600))
                        await self.delete(session)
                if dirty:
                    self._save()
            except Exception:                       # noqa: BLE001 - the reaper must not die
                log.exception("reaper iteration failed")

    # --- proxy bookkeeping ---------------------------------------------------

    def touch(self, session: Session) -> None:
        session.last_seen = time.time()

    def conn_opened(self, session: Session) -> None:
        if session.active_conns == 0:
            # First viewer in: start the clock. Two people watching the same
            # session is still one session being played, so the count only
            # matters for whether anybody is there at all.
            session.played_since = time.time()
        session.active_conns += 1
        session.last_seen = time.time()

    def conn_closed(self, session: Session) -> None:
        session.active_conns = max(0, session.active_conns - 1)
        if session.active_conns == 0 and session.played_since:
            session.played_seconds += max(0.0, time.time() - session.played_since)
            session.played_since = 0.0
            self._save()
        session.last_seen = time.time()
