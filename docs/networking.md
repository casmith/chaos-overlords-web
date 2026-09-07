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

## Current status

| Path | Port | State |
|---|---|---|
| Browser streaming (Selkies, WebSocket) | 8080 in-container | implemented |
| Diagnostic view (raw VNC, no audio) | 5900 in-container | off unless `ENABLE_VNC=true` |
| Chaos Overlords multiplayer | **unknown — do not guess** | Phase 4/5 |

### The streaming port

Selkies uses its **WebSocket transport**, not WebRTC. That means one TCP port
carries everything — the web client, the H.264 video, the Opus audio and every
input event. There is no signalling server, no media port range, and no
STUN/TURN to arrange. Compose maps one host port per player:

```
http://docker-host:8081   ->  chaos1:8080
http://docker-host:8082   ->  chaos2:8080
```

WebRTC is available in Selkies as an opt-in transport but is not used here: it
would add a UDP port range and TURN traversal for no benefit on a turn-based
game where 150 ms of input latency is comfortable (SPEC section 37).

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

The container serves HTTPS on a self-signed certificate by default, because the
browser client needs a secure context to run anywhere but `localhost`. Behind a
proxy that terminates TLS, set `ENABLE_HTTPS=false` and let the proxy present
the real certificate — the container still never manages a publicly trusted one
(SPEC section 21).

Because the transport is plain WebSockets over one port, the proxy requirements
are the ordinary ones:

- **Forward the WebSocket upgrade.** `Upgrade` and `Connection` headers on
  `/api/websockets`, or on the whole prefix.
- **Do not buffer, and do not time the connection out.** A streaming WebSocket
  is idle-looking to a proxy that watches for request completion. Raise read
  timeouts well past the default 60 s.
- **Set `WEB_SUBFOLDER`** if the session is served under a path rather than at
  the root of a hostname. The web client reads its own prefix from the URL it
  was loaded from, so only the server needs telling.

Nginx, for one player at a subpath:

```nginx
location /chaos1/ {
    proxy_pass http://chaos1:8080/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

with `WEB_SUBFOLDER=/chaos1` in that container's environment.

Caddy needs no WebSocket configuration at all — `reverse_proxy chaos1:8080`
handles the upgrade — and Traefik needs only a router and service, with the
same advice about timeouts.

**None of these has been tested against a real deployment yet.** They follow
from the transport rather than from measurement; treat them as a starting point.

What is true regardless of proxy:

- The container listens on one port, HTTPS by default and HTTP when
  `ENABLE_HTTPS=false`.
- Nothing in the container assumes a particular external hostname.
- The streaming port is unauthenticated unless `WEB_PASSWORD` is set.

## Authentication

Selkies has built-in HTTP basic authentication, off by default in this image.
Set `WEB_PASSWORD` (and optionally `WEB_USER`, default `player`) to turn it on;
Selkies itself refuses to start with authentication enabled and no password, so
it is an explicit either/or rather than something that can be half-configured.
Verified: no credentials and a wrong password both return 401, the right one
returns 200.

Basic auth over plain HTTP sends the password in the clear, so it is only
meaningful behind TLS. It is a convenience, not a substitute for the options
below. The streaming service must not be treated as safe to expose publicly.
Put it behind one of:

- a VPN — WireGuard or Tailscale, the simplest option for a handful of players
- a forward-auth proxy — Authentik or Authelia
- reverse-proxy basic auth, at minimum

None of this is container code; it lives in front of the container.

## Outbound access

After the image is built and the Wine prefix is created, the containers need no
internet access at all. In a hardened deployment, deny outbound WAN traffic for
`chaos-net` at the firewall and leave only container-to-container and
proxy-to-container traffic open.
