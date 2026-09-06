#!/bin/bash
# Multiplayer network diagnostics (SPEC section 45).
#
# Run this inside a container while hosting or joining a TCP/IP game to learn
# what Chaos Overlords actually does on the wire. Do not guess the ports --
# record them from here and from a packet capture, then write them into
# docs/networking.md.
#
#   docker exec chaos1 detect-network.sh
set -uo pipefail
# shellcheck source=/dev/null
. /usr/local/bin/chaos-env

hr() { printf '\n=== %s ===\n' "$1"; }

hr "Container identity"
echo "hostname: $(hostname)"
echo "date:     $(date -Iseconds)"

hr "Addresses"
ip -o addr show 2>/dev/null || ifconfig -a 2>/dev/null || echo "(no ip/ifconfig available)"

hr "Routing table"
ip route show 2>/dev/null || route -n 2>/dev/null || echo "(unavailable)"

hr "Listening TCP sockets"
ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || echo "(unavailable)"

hr "Listening UDP sockets"
ss -lnup 2>/dev/null || netstat -lnup 2>/dev/null || echo "(unavailable)"

hr "Established connections"
ss -tnp state established 2>/dev/null || netstat -tnp 2>/dev/null || echo "(unavailable)"

hr "Wine processes"
ps -eo pid,user,etime,args 2>/dev/null | grep -iE 'wine|chaos' | grep -v grep || echo "(none)"

hr "Packet capture hints"
cat <<'HINTS'
tcpdump is not installed in the runtime image. To capture traffic, run it on the
Docker host against the bridge interface for the chaos network:

  # find the bridge (br-<id>) that backs the compose network
  docker network inspect chaos-net -f '{{.Id}}' | cut -c1-12

  sudo tcpdump -i br-<id> -n -w /tmp/chaos.pcap

Then, from the host, watch what a hosting container listens on:

  docker exec chaos1 ss -lntup

Record for docs/networking.md:
  - TCP ports the host instance opens
  - UDP ports either instance opens
  - whether any broadcast (255.255.255.255 / subnet broadcast) traffic appears
  - the source/destination pattern once a client joins
HINTS
