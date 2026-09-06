# Networking

There are two completely separate network paths in this system, and confusing
them is the most common source of misconfiguration (SPEC section 23).

```
Player browser  ──HTTPS/WebRTC──►  container's streaming service
                                     (Phase 2; Phase 1 uses raw VNC)

chaos1 (Wine) ◄──── Chaos Overlords TCP/IP multiplayer ────► chaos2 (Wine)
                    entirely inside the Docker network
```

A remote player needs to reach only the **first** path. The game's own
multiplayer traffic stays on the container network and never has to be exposed
to the internet. This is the preferred deployment model.

## Phase 1 status

Phase 1 does not implement browser streaming or multiplayer testing. What exists
today:

| Path | Port | State |
|---|---|---|
| Diagnostic view (raw VNC, no audio) | 5900 in-container | implemented, dev only |
| Browser streaming (Selkies) | TBD | Phase 2 |
| Chaos Overlords multiplayer | **unknown — do not guess** | Phase 4/5 |

## Container-to-container

All containers join the `chaos-net` bridge network defined in
`docker-compose.yml`. Docker's embedded DNS resolves container names, so
`chaos1` and `chaos2` can reach each other by name, and by IP on the bridge
subnet:

```bash
docker network inspect chaos-net
docker exec chaos1 getent hosts chaos2
docker exec chaos1 ping -c1 chaos2      # if iputils is present
```

Start with bridge networking (SPEC rule 11). Only move to macvlan/ipvlan if
Phase 5 testing proves the game needs to appear as an independent host on the
physical LAN — for example if it uses subnet broadcast for game discovery and
the join dialog offers no way to type an IP address.

## Multiplayer ports

**Not yet determined.** Per SPEC rule 13 these must be discovered, not guessed.
The procedure is in [multiplayer-testing.md](multiplayer-testing.md); the
findings get recorded here once Phase 4/5 runs.

<!-- Fill in during Phase 5:
| Protocol | Port(s) | Direction | Notes |
|---|---|---|---|
| TCP | ? | client → host | |
| UDP | ? | ? | |
| Broadcast | ? | | |
-->

## macvlan (only if required)

If broadcast discovery turns out to be mandatory, each container can be given
its own address on the physical LAN. Sketch, to be validated in Phase 5:

```yaml
networks:
  chaos-lan:
    driver: macvlan
    driver_opts:
      parent: eth0          # the host NIC on the game VLAN
    ipam:
      config:
        - subnet: 192.168.15.0/24
          gateway: 192.168.15.1
          ip_range: 192.168.15.16/28
```

```yaml
services:
  chaos1:
    networks:
      chaos-lan:
        ipv4_address: 192.168.15.21
```

Two caveats worth knowing before going down this road:

- With macvlan, the **Docker host itself cannot reach the containers** over that
  interface without an extra macvlan shim interface. If you browse to the
  streaming URL from the Docker host, keep the containers on the bridge network
  as well.
- The parent interface must permit promiscuous mode. Most virtualised NICs and
  many wireless drivers do not.

## Reverse proxy

TLS terminates at the proxy; the container never manages certificates
(SPEC section 21). The streaming service needs WebSocket upgrade support and,
for WebRTC, either a permissive UDP path or a TURN relay. Concrete
configurations belong with the Phase 2 streaming implementation, so they are
deliberately not written here yet — the required headers depend on which
Selkies transport ends up in use.

What is already true regardless of proxy:

- The container listens on plain HTTP/VNC only, on the container network.
- Nothing in the container assumes a particular external hostname or path.

## Authentication

The streaming service must not be treated as safe to expose publicly. Put it
behind one of:

- a VPN — WireGuard or Tailscale, the simplest option for a handful of players
- a forward-auth proxy — Authentik or Authelia
- reverse-proxy basic auth, at minimum

None of this is container code; it lives in front of the container.

## Outbound access

After the image is built and the Wine prefix is created, the containers need no
internet access at all. In a hardened deployment, deny outbound WAN traffic for
`chaos-net` at the firewall and leave only container-to-container and
proxy-to-container traffic open.
