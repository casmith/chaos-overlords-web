# Multiplayer testing procedure

**Status: not yet executed.** This is the plan for Phases 4 and 5. Fill in the
Findings section as you go, and copy the conclusions into
[networking.md](networking.md).

Do not guess the game's ports. Observe them.

## Setup

```bash
docker compose up -d chaos1 chaos2
docker compose ps
```

Connect a viewer to each container (Phase 1: VNC on 5901 / 5902; Phase 2
onwards: the browser URL).

## Step 1 — baseline

Before touching the multiplayer menu, record what each container already has
open, so that anything new is obviously the game:

```bash
docker exec chaos1 detect-network.sh > /tmp/chaos1-before.txt
docker exec chaos2 detect-network.sh > /tmp/chaos2-before.txt
```

## Step 2 — host a game

On chaos1, start a TCP/IP multiplayer game and stop at the lobby. Then:

```bash
docker exec chaos1 detect-network.sh > /tmp/chaos1-hosting.txt
diff /tmp/chaos1-before.txt /tmp/chaos1-hosting.txt
```

Record every socket that appeared:

- protocol (TCP or UDP)
- port number
- bind address (`0.0.0.0` vs a specific address vs `127.0.0.1` — a
  loopback-only bind means the game will never work across containers as-is)

## Step 3 — capture while joining

Start a capture on the host before joining, so the discovery phase is included:

```bash
BR=br-$(docker network inspect chaos-net -f '{{.Id}}' | cut -c1-12)
sudo tcpdump -i "$BR" -n -s0 -w /tmp/chaos-join.pcap &
```

On chaos2, join the game. If the dialog asks for an address, use `chaos1` or the
bridge IP from `docker network inspect chaos-net`. If it offers only a browse or
"look for games" button, that is itself the finding: the game relies on
discovery rather than a typed address.

```bash
sudo kill %1
tcpdump -nr /tmp/chaos-join.pcap | head -50
```

Look for:

- the first packet exchanged, and which side sends it
- broadcast destinations (`255.255.255.255` or the subnet broadcast) — these are
  what bridge networking is most likely to break
- DirectPlay's traditional ports (2300–2400 TCP/UDP, 47624 TCP) — if these
  appear, the game uses DirectPlay and Wine's `dplayx` is in the path
- whether traffic continues on the discovery port or moves to a new one

## Step 4 — play a full game

Confirm stability, not just connection: play through several complete turns with
both instances open, and confirm turn state synchronises in both directions.
Watch `docker logs` on both containers for Wine errors during play.

## Step 5 — decide the network mode

- Direct TCP/IP with a typed address, no broadcast → **keep bridge networking**.
- Broadcast discovery only, with no way to enter an address → try macvlan and
  re-run steps 2–4.
- Broadcast discovery *plus* an address entry field → prefer bridge and document
  that players must type an address.

## Findings

_Not yet gathered._

```
Date:
Wine version:
Windows version reported to the game:

Host instance listening sockets:
Client instance sockets:
Broadcast observed:            yes / no
Address entry available:       yes / no
DirectPlay in use:             yes / no
Bridge networking sufficient:  yes / no
Stable for a full game:        yes / no
```

## Known unknowns

Worth being ready for, none of them confirmed yet:

- **DirectPlay.** A 1997 Windows title may use DirectPlay rather than raw
  sockets. Wine's built-in `dplayx` is incomplete, and the shipped registry's
  `commType=0` hints the game has more than one communications backend. If
  DirectPlay is in play, the winetricks `directplay` verb (native DirectX
  DirectPlay DLLs) is the first thing to try — and would be the first
  winetricks component this project adds, per SPEC section 14.
- **Player identity.** Instances share a Wine prefix layout but have separate
  volumes, so player names and settings are already independent.
- **Save synchronisation** is explicitly out of scope (SPEC section 3).
