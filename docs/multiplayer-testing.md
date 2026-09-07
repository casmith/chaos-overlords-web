# Multiplayer testing procedure

**Status: connectivity established.** Two containers have been connected host
to joiner; the protocol and port are recorded in
[multiplayer-findings.md](multiplayer-findings.md) and
[networking.md](networking.md). What remains is playing a full game to
completion and trying three and four players.

Read [multiplayer-findings.md](multiplayer-findings.md) first — it covers the
non-obvious `Comm → WinSock` step and the Wine dialog bug that made both hosting
and joining look like a hang.

Do not guess the game's ports. Observe them.

## Setup

```bash
docker compose up -d chaos1 chaos2
docker compose ps
```

Open each player's session in a browser: http://docker-host:8081 and
http://docker-host:8082.

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

```
Wine version:                  10.0 (Debian 10.0~repack-6)
Windows version:               win98

Host listening socket:         0.0.0.0:4269 TCP, listen backlog 1
Joiner socket:                 ephemeral -> host:4269 TCP
Broadcast observed:            no
Address entry available:       yes ("Please enter the IP Address of the Host")
DirectPlay in use:             no (WSOCK32.dll, no dplayx import)
Bridge networking sufficient:  yes
Stable for a full game:        NOT YET TESTED
```

## Resolved unknowns

- **DirectPlay** is not used. The game imports `WSOCK32.dll` and no `dplayx`,
  so Wine's incomplete DirectPlay is not in the path and no winetricks
  component is needed.
- **`commType=0`** in the shipped registry does mean a communications backend
  choice — the `Comm` menu offers None / WinSock / Modem / Direct Connect, and
  it ships on **None**, which greys out Host and Join. Select WinSock first.
- **Player identity.** Separate `/config` volumes already keep player names and
  settings independent.
- **Save synchronisation** remains out of scope (SPEC section 3).
